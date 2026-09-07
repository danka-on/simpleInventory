"""Server metrics for Sweet Shelves."""

import datetime
import os
import re
import subprocess
import threading
import time
from DBmanager import connect_db
from pathlib import Path
from . import config as ss_config


_SERVER_METRICS_DB_NAME = 'server_metrics.db'


_server_metrics_thread_started = False


def _server_metrics_env_int(name, default, minimum, maximum):
    raw = os.getenv(name)
    try:
        value = int(str(raw).strip()) if raw is not None else int(default)
    except Exception:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


def _ensure_server_metrics_schema(cur):
    cur.execute('PRAGMA journal_mode=WAL')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS system_metric_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at TEXT NOT NULL,
            collected_ts INTEGER NOT NULL,
            cpu_percent REAL,
            memory_percent REAL,
            memory_used_mb REAL,
            memory_total_mb REAL,
            disk_percent REAL,
            disk_used_gb REAL,
            disk_total_gb REAL,
            disk_free_gb REAL,
            temp_c REAL
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_system_metric_snapshots_ts ON system_metric_snapshots(collected_ts)')


def _server_metrics_cpu_percent(sample_seconds=0.2):
    try:
        import psutil
    except Exception:
        psutil = None

    if psutil is not None:
        try:
            return round(float(psutil.cpu_percent(interval=max(0.1, float(sample_seconds)))), 1)
        except Exception:
            pass

    if os.name == 'nt':
        return None

    def _sample_proc_stat():
        with open('/proc/stat', 'r', encoding='utf-8') as handle:
            parts = handle.readline().split()[1:]
        values = [int(value) for value in parts[:8]]
        idle = values[3] + values[4]
        total = sum(values)
        return idle, total

    try:
        idle_one, total_one = _sample_proc_stat()
        time.sleep(max(0.1, float(sample_seconds)))
        idle_two, total_two = _sample_proc_stat()
        total_delta = total_two - total_one
        idle_delta = idle_two - idle_one
        if total_delta <= 0:
            return None
        return round(((total_delta - idle_delta) * 100.0) / total_delta, 1)
    except Exception:
        return None


def _server_metrics_memory_stats():
    try:
        import psutil
    except Exception:
        psutil = None

    if psutil is not None:
        try:
            mem = psutil.virtual_memory()
            return {
                'total_mb': round(mem.total / (1024 ** 2), 1),
                'used_mb': round(mem.used / (1024 ** 2), 1),
                'percent': round(float(mem.percent), 1),
            }
        except Exception:
            pass

    if os.name == 'nt':
        return {'total_mb': None, 'used_mb': None, 'percent': None}

    info = {}
    try:
        with open('/proc/meminfo', 'r', encoding='utf-8') as handle:
            for line in handle:
                key, _, raw_value = line.partition(':')
                info[key] = int(raw_value.strip().split()[0])
        total = info.get('MemTotal', 0) / 1024.0
        available = info.get('MemAvailable', 0) / 1024.0
        used = max(total - available, 0.0)
        percent = round((used / total) * 100.0, 1) if total else None
        return {
            'total_mb': round(total, 1),
            'used_mb': round(used, 1),
            'percent': percent,
        }
    except Exception:
        return {'total_mb': None, 'used_mb': None, 'percent': None}


def _server_metrics_disk_stats():
    import shutil

    try:
        disk_root = str(ss_config.BASE_DIR.anchor or ss_config.BASE_DIR)
        total, used, free = shutil.disk_usage(disk_root)
        percent = round((used / total) * 100.0, 1) if total else None
        return {
            'total_gb': round(total / (1024 ** 3), 2),
            'used_gb': round(used / (1024 ** 3), 2),
            'free_gb': round(free / (1024 ** 3), 2),
            'percent': percent,
        }
    except Exception:
        return {'total_gb': None, 'used_gb': None, 'free_gb': None, 'percent': None}


def _server_metrics_cpu_temp_c():
    try:
        import psutil
    except Exception:
        psutil = None

    if psutil is not None:
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for name in ('cpu_thermal', 'cpu-thermal', 'coretemp', 'k10temp'):
                    if name in temps and temps[name]:
                        return round(float(temps[name][0].current), 1)
                for sensors in temps.values():
                    if sensors:
                        return round(float(sensors[0].current), 1)
        except Exception:
            pass

    for sensor_path in (
        '/sys/class/thermal/thermal_zone0/temp',
        '/sys/class/hwmon/hwmon0/temp1_input',
    ):
        try:
            raw = Path(sensor_path).read_text(encoding='utf-8', errors='ignore').strip()
            if raw:
                return round(float(raw) / 1000.0, 1)
        except Exception:
            pass

    try:
        result = subprocess.run(
            ['vcgencmd', 'measure_temp'],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            match = re.search(r'([0-9]+(?:\.[0-9]+)?)', result.stdout or '')
            if match:
                return round(float(match.group(1)), 1)
    except Exception:
        pass

    return None


def _collect_server_metrics_snapshot():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    memory = _server_metrics_memory_stats()
    disk = _server_metrics_disk_stats()
    return {
        'collected_at': now_utc.isoformat(),
        'collected_ts': int(now_utc.timestamp()),
        'cpu_percent': _server_metrics_cpu_percent(),
        'memory_percent': memory.get('percent'),
        'memory_used_mb': memory.get('used_mb'),
        'memory_total_mb': memory.get('total_mb'),
        'disk_percent': disk.get('percent'),
        'disk_used_gb': disk.get('used_gb'),
        'disk_total_gb': disk.get('total_gb'),
        'disk_free_gb': disk.get('free_gb'),
        'temp_c': _server_metrics_cpu_temp_c(),
    }


def _record_server_metrics_snapshot(force=False):
    interval_seconds = _server_metrics_env_int('SERVER_METRICS_SNAPSHOT_INTERVAL_SECONDS', 300, 60, 3600)
    # Keep enough data for the console's 180d/1y/2y/All history choices.
    # At the default five-minute cadence, two years is roughly 210k compact rows.
    retention_days = _server_metrics_env_int('SERVER_METRICS_RETENTION_DAYS', 730, 1, 3650)
    snapshot = _collect_server_metrics_snapshot()

    with connect_db(_SERVER_METRICS_DB_NAME) as conn:
        cur = conn.cursor()
        _ensure_server_metrics_schema(cur)
        cur.execute('SELECT collected_ts FROM system_metric_snapshots ORDER BY collected_ts DESC LIMIT 1')
        row = cur.fetchone()
        if row and not force:
            try:
                latest_ts = int(row[0] or 0)
            except Exception:
                latest_ts = 0
            if latest_ts and (snapshot['collected_ts'] - latest_ts) < interval_seconds:
                return False

        cur.execute('''
            INSERT INTO system_metric_snapshots (
                collected_at,
                collected_ts,
                cpu_percent,
                memory_percent,
                memory_used_mb,
                memory_total_mb,
                disk_percent,
                disk_used_gb,
                disk_total_gb,
                disk_free_gb,
                temp_c
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            snapshot['collected_at'],
            snapshot['collected_ts'],
            snapshot['cpu_percent'],
            snapshot['memory_percent'],
            snapshot['memory_used_mb'],
            snapshot['memory_total_mb'],
            snapshot['disk_percent'],
            snapshot['disk_used_gb'],
            snapshot['disk_total_gb'],
            snapshot['disk_free_gb'],
            snapshot['temp_c'],
        ))

        cutoff_ts = snapshot['collected_ts'] - (retention_days * 86400)
        cur.execute('DELETE FROM system_metric_snapshots WHERE collected_ts < ?', (cutoff_ts,))

    return True


def server_metrics_history_worker():
    """Persist lightweight CPU/memory/disk/temp snapshots for the remote console."""
    import time as _time

    interval_seconds = _server_metrics_env_int('SERVER_METRICS_SNAPSHOT_INTERVAL_SECONDS', 300, 60, 3600)
    startup_delay = _server_metrics_env_int('SERVER_METRICS_STARTUP_DELAY_SECONDS', 20, 0, 600)
    print(f"📈 Server metrics history worker started (every {max(1, interval_seconds // 60)} minutes)")

    if startup_delay > 0:
        _time.sleep(startup_delay)

    while True:
        loop_started = _time.time()
        try:
            _record_server_metrics_snapshot()
        except Exception as e:
            print(f"⚠️ Server metrics history worker error: {e}")

        elapsed = _time.time() - loop_started
        sleep_for = max(30, interval_seconds - int(elapsed))
        _time.sleep(sleep_for)


def _start_server_metrics_history_thread():
    """Start lightweight server metrics snapshotting for chart history."""
    global _server_metrics_thread_started
    if _server_metrics_thread_started:
        return
    _server_metrics_thread_started = True
    metrics_thread = threading.Thread(target=server_metrics_history_worker, daemon=True)
    metrics_thread.start()
    print("🚀 Server metrics history thread started")
