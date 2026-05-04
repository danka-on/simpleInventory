import argparse
import ctypes
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_TITLE = "Sweet Shelves Server Console"
CONFIG_FILENAME = "server_control_config.json"
EXAMPLE_CONFIG_FILENAME = "server_control_config.example.json"
REFRESH_INTERVAL_MS = 5000
LOG_REFRESH_INTERVAL_MS = 6000
HISTORY_REFRESH_INTERVAL_MS = 60000
DEFAULT_KEY_PATH = str(Path.home() / ".ssh" / "sweet_shelves_pi")
HISTORY_RANGE_OPTIONS = {
    "24h": 24,
    "3d": 72,
    "7d": 168,
    "30d": 720,
    "90d": 2160,
}

# ── Catppuccin Mocha palette ─────────────────────────────────────────────────
BASE     = "#1e1e2e"   # window background
MANTLE   = "#181825"   # header / deeper bg
CRUST    = "#11111b"   # deepest surfaces
SURFACE0 = "#313244"   # card / panel surface
SURFACE1 = "#45475a"   # elevated hover surface
SURFACE2 = "#585b70"   # borders, separators
TEXT     = "#cdd6f4"   # primary text
SUBTEXT1 = "#bac2de"   # secondary text
SUBTEXT0 = "#a6adc8"   # muted / label text
OVERLAY  = "#7f849c"   # very muted
BLUE     = "#89b4fa"   # primary accent / info
SAPPHIRE = "#74c7ec"   # pull / terminal / secondary
GREEN    = "#a6e3a1"   # success / live / backup
YELLOW   = "#f9e2af"   # warning
PEACH    = "#fab387"   # restore / caution
RED      = "#f38ba8"   # danger / down
MAUVE    = "#cba6f7"   # accent alt


DEFAULT_CONFIG = {
    "connection": {
        "host": "10.0.0.151",
        "user": "dk",
        "port": 22,
        "key_path": DEFAULT_KEY_PATH if Path(DEFAULT_KEY_PATH).exists() else "",
        "connect_timeout_seconds": 8,
    },
    "commands": [
        {
            "label": "Restart Sweet Shelves",
            "command": "sudo systemctl restart sweetshelves.service",
            "section": "Sweet Shelves",
            "tone": "accent",
            "confirm": False,
        },
        {
            "label": "Service Status",
            "command": "systemctl status sweetshelves.service --no-pager",
            "section": "Sweet Shelves",
            "tone": "secondary",
            "confirm": False,
        },
        {
            "label": "Pull Sweet Shelves Updates",
            "command": "cd /opt/sweetshelves && git pull",
            "section": "Sweet Shelves",
            "tone": "secondary",
            "confirm": False,
        },
        {
            "label": "Backup + Pull + Restart",
            "command": "sh -lc 'cd /opt/sweetshelves && python3 rotating_backup.py backup && git pull && sudo systemctl restart sweetshelves.service'",
            "section": "Sweet Shelves",
            "tone": "accent",
            "confirm": True,
        },
        {
            "label": "Backup Status & Health",
            "command": "sh -lc 'echo \"Recent cron entries:\"; crontab -l 2>/dev/null || echo \"No user cron entries\"; echo; echo \"Recent backups:\"; python3 /opt/sweetshelves/rotating_backup.py list 2>/dev/null | head -n 30 || echo \"Backup list unavailable\"; echo; echo \"Storage:\"; df -h /opt/sweetshelves /mnt /media 2>/dev/null'",
            "section": "Sweet Shelves",
            "tone": "secondary",
            "confirm": False,
        },
        {
            "label": "Run Backup Now",
            "command": "cd /opt/sweetshelves && python3 rotating_backup.py backup",
            "section": "Sweet Shelves",
            "tone": "success",
            "confirm": False,
        },
        {
            "label": "List Backups",
            "command": "cd /opt/sweetshelves && python3 rotating_backup.py list",
            "section": "Sweet Shelves",
            "tone": "secondary",
            "confirm": False,
        },
        {
            "label": "Restore Latest Backup",
            "command": "cd /opt/sweetshelves && bash ./restore_searchrack.sh",
            "section": "Sweet Shelves",
            "tone": "warning",
            "confirm": True,
        },
        {
            "label": "Backup/Pull Status",
            "command": "sh -lc 'echo \"Latest backup:\"; latest=$(ls -t /mnt/db-backups/sweetshelves-db-backups/daily/searchRack-*.sqlite3.gz 2>/dev/null | head -1 || true); if [ -n \"$latest\" ]; then stat -c \"%y  %n\" \"$latest\"; else echo \"No backup found in /mnt/db-backups/sweetshelves-db-backups/daily\"; fi; echo; echo \"Recent pull activity:\"; git -C /opt/sweetshelves reflog --date=local --grep-reflog=\"pull\" -n 5 2>/dev/null || echo \"No git pull reflog entries found\"; echo; echo \"Current code version:\"; git -C /opt/sweetshelves log -1 --date=local --pretty=format:\"%ad  %h  %s\"; echo; echo; echo \"Service status:\"; systemctl is-active sweetshelves.service; systemctl status sweetshelves.service --no-pager -n 3 | tail -n 3'",
            "section": "Sweet Shelves",
            "tone": "info",
            "confirm": False,
        },
        {
            "label": "Backup Helper",
            "command": "sh -lc 'echo \"Create backup now:\"; echo \"  cd /opt/sweetshelves && python3 rotating_backup.py backup\"; echo; echo \"List backups:\"; echo \"  cd /opt/sweetshelves && python3 rotating_backup.py list\"; echo; echo \"Restore helper:\"; sed -n \"1,120p\" /opt/sweetshelves/restore_searchrack.sh 2>/dev/null || echo \"restore_searchrack.sh not found\"'",
            "section": "Sweet Shelves",
            "tone": "secondary",
            "confirm": False,
        },
        {
            "label": "Check Disk",
            "command": "df -h /",
            "section": "Server",
            "tone": "secondary",
            "confirm": False,
        },
        {
            "label": "USB Stick Size",
            "command": "sh -lc 'lsblk -o NAME,TRAN,SIZE,FSTYPE,MOUNTPOINT,LABEL,MODEL | { head -n 1; grep -i usb || true; }'",
            "section": "Server",
            "tone": "secondary",
            "confirm": False,
        },
        {
            "label": "Server Status",
            "command": "hostname && uptime && free -h",
            "section": "Server",
            "tone": "secondary",
            "confirm": False,
        },
        {
            "label": "Open Server Terminal",
            "action": "open_terminal",
            "section": "Server",
            "tone": "info",
            "confirm": False,
        },
        {
            "label": "Reboot Server",
            "command": "sudo reboot",
            "section": "Server",
            "tone": "danger",
            "confirm": True,
        },
    ],
    "logs": [
        {
            "name": "Sweet Shelves Service",
            "command": "journalctl -u sweetshelves.service -n 80 --no-pager",
        },
        {
            "name": "System Log",
            "command": "tail -n 80 /var/log/syslog",
        },
        {
            "name": "Kernel Messages",
            "command": "dmesg -T | tail -n 80",
        },
    ],
}


REMOTE_STATS_SCRIPT = r"""
python3 - <<'PY'
import json
import os
import re
import shutil
import socket
import subprocess
import time


def run(cmd):
    try:
        return subprocess.check_output(
            cmd,
            shell=True,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=4,
        ).strip()
    except Exception:
        return ""


def read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return handle.read().strip()
    except Exception:
        return ""


def cpu_percent():
    def sample():
        with open("/proc/stat", "r", encoding="utf-8") as handle:
            parts = handle.readline().split()[1:]
        values = [int(value) for value in parts[:8]]
        idle = values[3] + values[4]
        total = sum(values)
        return idle, total

    try:
        idle_one, total_one = sample()
        time.sleep(0.25)
        idle_two, total_two = sample()
        total_delta = total_two - total_one
        idle_delta = idle_two - idle_one
        if total_delta <= 0:
            return None
        return round(((total_delta - idle_delta) * 100.0) / total_delta, 1)
    except Exception:
        return None


def memory_stats():
    info = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                key, _, raw_value = line.partition(":")
                info[key] = int(raw_value.strip().split()[0])
        total = info.get("MemTotal", 0) / 1024
        available = info.get("MemAvailable", 0) / 1024
        used = max(total - available, 0)
        percent = round((used / total) * 100, 1) if total else None
        return {
            "total_mb": round(total, 1),
            "used_mb": round(used, 1),
            "percent": percent,
        }
    except Exception:
        return {"total_mb": None, "used_mb": None, "percent": None}


def disk_stats():
    try:
        total, used, free = shutil.disk_usage("/")
        percent = round((used / total) * 100, 1) if total else None
        return {
            "total_gb": round(total / (1024 ** 3), 1),
            "used_gb": round(used / (1024 ** 3), 1),
            "free_gb": round(free / (1024 ** 3), 1),
            "percent": percent,
        }
    except Exception:
        return {"total_gb": None, "used_gb": None, "free_gb": None, "percent": None}


def load_average():
    try:
        loads = os.getloadavg()
        return [round(value, 2) for value in loads]
    except Exception:
        return []


def uptime_string():
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as handle:
            total_seconds = float(handle.read().split()[0])
        total_seconds = int(total_seconds)
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, _ = divmod(remainder, 60)
        if days:
            return f"{days}d {hours}h {minutes}m"
        return f"{hours}h {minutes}m"
    except Exception:
        return "Unavailable"


def cpu_temperature():
    candidates = [
        "/sys/class/thermal/thermal_zone0/temp",
        "/sys/class/hwmon/hwmon0/temp1_input",
    ]
    for path in candidates:
        raw = read(path)
        if raw:
            try:
                value = float(raw) / 1000.0
                return round(value, 1)
            except Exception:
                pass

    vcgencmd_output = run("vcgencmd measure_temp")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", vcgencmd_output)
    if match:
        return round(float(match.group(1)), 1)

    sensors_output = run("sensors")
    match = re.search(r"\+?([0-9]+(?:\.[0-9]+)?)°C", sensors_output)
    if match:
        return round(float(match.group(1)), 1)
    return None


def usb_stats():
    candidates = [
        "/mnt/db-backups",
        "/mnt/db-backups/sweetshelves-db-backups",
        "/media/dk/USB",
        "/media/dk",
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            total, used, free = shutil.disk_usage(path)
            percent = round((used / total) * 100, 1) if total else None
            return {
                "path": path,
                "total_gb": round(total / (1024 ** 3), 1),
                "used_gb": round(used / (1024 ** 3), 1),
                "free_gb": round(free / (1024 ** 3), 1),
                "percent": percent,
            }
        except Exception:
            continue
    return {"path": None, "total_gb": None, "used_gb": None, "free_gb": None, "percent": None}


def backup_health():
    candidates = [
        "/mnt/db-backups/sweetshelves-db-backups/daily",
        "/media/dk/USB/sweetshelves-db-backups",
        "/opt/sweetshelves/backups",
    ]
    found_dir = None
    for raw_path in candidates:
        path = raw_path
        if os.path.isdir(path):
            found_dir = path
            break

    cron_output = run("crontab -l 2>/dev/null")
    cron_present = "rotating_backup.py" in cron_output or "backup" in cron_output.lower()

    if not found_dir:
        return {
            "directory": None,
            "healthy_percent": 15 if cron_present else 0,
            "status": "No backup directory found",
            "latest_age_hours": None,
            "cron_present": cron_present,
        }

    files = []
    for name in os.listdir(found_dir):
        if name.endswith(".sqlite3.gz"):
            files.append(os.path.join(found_dir, name))

    if not files:
        return {
            "directory": found_dir,
            "healthy_percent": 20 if cron_present else 5,
            "status": "No backup files yet",
            "latest_age_hours": None,
            "cron_present": cron_present,
        }

    latest = max(files, key=os.path.getmtime)
    age_hours = round((time.time() - os.path.getmtime(latest)) / 3600.0, 1)
    if age_hours <= 24:
        percent = 100
        status = "Healthy"
    elif age_hours <= 48:
        percent = 65
        status = "Stale"
    else:
        percent = 25
        status = "Overdue"

    return {
        "directory": found_dir,
        "healthy_percent": percent,
        "status": status,
        "latest_age_hours": age_hours,
        "cron_present": cron_present,
    }


def git_info(repo_path):
    try:
        out = run(
            f"git -C {repo_path} log -1 --pretty=format:'%ar|%s' 2>/dev/null"
        ).strip("'")
        if "|" in out:
            when, subject = out.split("|", 1)
            return {"when": when.strip(), "subject": subject.strip()}
    except Exception:
        pass
    return {"when": None, "subject": None}


def git_info(repo_path):
    try:
        out = run(
            f"git -C {repo_path} log -1 --pretty=format:'%ar|%s' 2>/dev/null"
        ).strip("'")
        if "|" in out:
            when, subject = out.split("|", 1)
            return {"when": when.strip(), "subject": subject.strip()}
    except Exception:
        pass
    return {"when": None, "subject": None}


def service_status(service_name):
    active = run(f"systemctl is-active {service_name}").strip()
    is_active = active == "active"
    uptime_str = None
    if is_active:
        ts_raw = run(f"systemctl show {service_name} --property=ExecMainStartTimestampMonotonic --value").strip()
        if ts_raw:
            try:
                service_mono_us = int(ts_raw)
                current_mono_us = int(time.monotonic() * 1_000_000)
                elapsed_s = max(0, (current_mono_us - service_mono_us) // 1_000_000)
                days, rem = divmod(elapsed_s, 86400)
                hours, rem = divmod(rem, 3600)
                minutes, _ = divmod(rem, 60)
                if days:
                    uptime_str = f"{days}d {hours}h {minutes}m"
                elif hours:
                    uptime_str = f"{hours}h {minutes}m"
                else:
                    uptime_str = f"{minutes}m"
            except Exception:
                pass
    return {"active": is_active, "uptime": uptime_str}


payload = {
    "hostname": socket.gethostname(),
    "uptime": uptime_string(),
    "cpu_percent": cpu_percent(),
    "memory": memory_stats(),
    "disk": disk_stats(),
    "usb": usb_stats(),
    "backup": backup_health(),
    "temperature_c": cpu_temperature(),
    "load_average": load_average(),
    "service": service_status("sweetshelves.service"),
    "git": git_info("/opt/sweetshelves"),
    "timestamp": int(time.time()),
}

print(json.dumps(payload))
PY
""".strip()


REMOTE_HISTORY_SCRIPT_TEMPLATE = r"""
python3 - <<'PY'
import json
import os
import sqlite3
import time

db_path = "/opt/sweetshelves/server_metrics.db"
hours = __HOURS__
payload = {
    "status": "ok",
    "hours": hours,
    "db_path": db_path,
    "points": [],
    "count": 0,
}

if not os.path.exists(db_path):
    payload["status"] = "missing_db"
    print(json.dumps(payload))
    raise SystemExit

conn = None
try:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='system_metric_snapshots'"
    )
    if not cur.fetchone():
        payload["status"] = "missing_table"
        print(json.dumps(payload))
        raise SystemExit

    cutoff_ts = int(time.time()) - (hours * 3600)
    cur.execute(
        '''
        SELECT collected_at, collected_ts, cpu_percent, memory_percent, disk_percent, temp_c
        FROM system_metric_snapshots
        WHERE collected_ts >= ?
        ORDER BY collected_ts ASC
        ''',
        (cutoff_ts,)
    )
    rows = cur.fetchall()
    payload["points"] = [
        {
            "collected_at": row["collected_at"],
            "collected_ts": row["collected_ts"],
            "cpu_percent": row["cpu_percent"],
            "memory_percent": row["memory_percent"],
            "disk_percent": row["disk_percent"],
            "temp_c": row["temp_c"],
        }
        for row in rows
    ]
    payload["count"] = len(payload["points"])
    if payload["points"]:
        payload["latest"] = payload["points"][-1]
except Exception as exc:
    payload["status"] = "error"
    payload["error"] = str(exc)
finally:
    if conn is not None:
        conn.close()

print(json.dumps(payload))
PY
""".strip()


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def enable_windows_dpi_awareness():
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def config_path() -> Path:
    return app_root() / CONFIG_FILENAME


def example_config_path() -> Path:
    return app_root() / EXAMPLE_CONFIG_FILENAME


def deep_copy_default_config():
    return json.loads(json.dumps(DEFAULT_CONFIG))


def merged_config(raw_config):
    config = deep_copy_default_config()
    if not isinstance(raw_config, dict):
        return config

    connection = raw_config.get("connection")
    if isinstance(connection, dict):
        config["connection"].update(connection)

    if isinstance(raw_config.get("commands"), list) and raw_config["commands"]:
        config["commands"] = raw_config["commands"]

    if isinstance(raw_config.get("logs"), list) and raw_config["logs"]:
        config["logs"] = raw_config["logs"]

    return config


def load_or_create_config():
    path = config_path()
    if path.exists():
        try:
            return merged_config(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return deep_copy_default_config()

    config = deep_copy_default_config()
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    example = example_config_path()
    if not example.exists():
        example.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config


def save_config(config):
    config_path().write_text(json.dumps(config, indent=2), encoding="utf-8")


class SSHClient:
    def __init__(self, config):
        self.config = config

    def update_config(self, config):
        self.config = config

    def _build_command(self, remote_command):
        connection = self.config["connection"]
        target = f'{connection["user"]}@{connection["host"]}'
        cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f'ConnectTimeout={int(connection.get("connect_timeout_seconds", 8) or 8)}',
            "-p",
            str(connection.get("port", 22)),
        ]
        key_path = str(connection.get("key_path", "")).strip()
        if key_path:
            cmd.extend(["-i", key_path])
        cmd.extend([target, remote_command])
        return cmd

    def run(self, remote_command, timeout=20):
        command = self._build_command(remote_command)
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


# ── Reusable UI widgets ───────────────────────────────────────────────────────

class StatusTile(tk.Frame):
    """Large status tile — shown at the top for Sweet Shelves and Server."""

    _WAIT_COLOR = OVERLAY

    def __init__(self, master, label, icon="●"):
        super().__init__(master, bg=SURFACE0)
        self._icon = icon
        # Left accent bar
        self._accent_bar = tk.Frame(self, bg=SURFACE2, width=5)
        self._accent_bar.pack(side="left", fill="y")
        self._accent_bar.pack_propagate(False)
        # Content
        body = tk.Frame(self, bg=SURFACE0)
        body.pack(side="left", fill="both", expand=True, padx=(16, 18), pady=16)
        tk.Label(body, text=label.upper(),
                 font=("Segoe UI", 8, "bold"),
                 fg=SUBTEXT0, bg=SURFACE0).pack(anchor="w")
        self._status_lbl = tk.Label(body, text=f"{icon}  —",
                                    font=("Segoe UI Semibold", 20),
                                    fg=self._WAIT_COLOR, bg=SURFACE0)
        self._status_lbl.pack(anchor="w", pady=(5, 3))
        self._detail_lbl = tk.Label(body, text="Waiting for connection...",
                                    font=("Segoe UI", 9),
                                    fg=SUBTEXT0, bg=SURFACE0,
                                    wraplength=320, justify="left")
        self._detail_lbl.pack(anchor="w")

    def set_live(self, status_text, status_color, detail_text, accent_color):
        self._accent_bar.configure(bg=accent_color)
        self._status_lbl.configure(text=f"{self._icon}  {status_text}", fg=status_color)
        self._detail_lbl.configure(text=detail_text)

    def set_waiting(self):
        self._accent_bar.configure(bg=SURFACE2)
        self._status_lbl.configure(text=f"{self._icon}  —", fg=self._WAIT_COLOR)
        self._detail_lbl.configure(text="Waiting for connection...")


class MetricCard(tk.Frame):
    """Compact metric card with thin progress bar — used in the metrics row."""

    def __init__(self, master, title, bar_color):
        super().__init__(master, bg=SURFACE0)
        self._bar_color = bar_color
        self._pct = None
        inner = tk.Frame(self, bg=SURFACE0)
        inner.pack(fill="both", expand=True, padx=14, pady=12)
        tk.Label(inner, text=title.upper(),
                 font=("Segoe UI", 8, "bold"),
                 fg=SUBTEXT0, bg=SURFACE0).pack(anchor="w")
        self._val_lbl = tk.Label(inner, text="—",
                                 font=("Segoe UI Semibold", 22),
                                 fg=TEXT, bg=SURFACE0)
        self._val_lbl.pack(anchor="w", pady=(4, 5))
        self._canvas = tk.Canvas(inner, height=5, bg=SURFACE0, highlightthickness=0)
        self._canvas.pack(fill="x", pady=(0, 7))
        self._canvas.bind("<Configure>", lambda _e: self._redraw())
        self._sub_lbl = tk.Label(inner, text="—",
                                 font=("Segoe UI", 8),
                                 fg=SUBTEXT0, bg=SURFACE0,
                                 wraplength=160, justify="left")
        self._sub_lbl.pack(anchor="w")

    def _redraw(self):
        w = self._canvas.winfo_width() or 10
        h = 5
        self._canvas.delete("all")
        self._canvas.create_rectangle(0, 0, w, h, fill=SURFACE1, outline="")
        if self._pct is not None and self._pct > 0:
            fw = max(1, int(w * (self._pct / 100.0)))
            self._canvas.create_rectangle(0, 0, fw, h, fill=self._bar_color, outline="")

    def set_value(self, percent, value_text, subtitle_text):
        self._val_lbl.configure(text=value_text)
        self._sub_lbl.configure(text=subtitle_text)
        self._pct = percent if isinstance(percent, (int, float)) else None
        self._redraw()


def build_remote_history_script(hours):
    safe_hours = max(1, min(int(hours or 24), 24 * 365))
    return REMOTE_HISTORY_SCRIPT_TEMPLATE.replace("__HOURS__", str(safe_hours))


class HistoryChart(tk.Frame):
    """Simple canvas line chart for lightweight remote system history."""

    def __init__(self, master, title, line_color, *, max_value=None, unit=""):
        super().__init__(master, bg=SURFACE0)
        self._title = title
        self._line_color = line_color
        self._max_value = max_value
        self._unit = unit
        self._points = []

        inner = tk.Frame(self, bg=SURFACE0)
        inner.pack(fill="both", expand=True, padx=14, pady=12)
        tk.Label(inner, text=title.upper(),
                 font=("Segoe UI", 8, "bold"),
                 fg=SUBTEXT0, bg=SURFACE0).pack(anchor="w")
        self._summary_lbl = tk.Label(inner, text="Waiting for history...",
                                     font=("Segoe UI", 8),
                                     fg=SUBTEXT0, bg=SURFACE0,
                                     justify="left", wraplength=420)
        self._summary_lbl.pack(anchor="w", pady=(4, 8))
        self._canvas = tk.Canvas(inner, height=180, bg=SURFACE0, highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Configure>", lambda _e: self._redraw())

    def set_points(self, points, summary_text=None):
        clean_points = []
        for ts, value in points:
            if not isinstance(ts, (int, float)) or not isinstance(value, (int, float)):
                continue
            clean_points.append((int(ts), float(value)))
        self._points = clean_points
        self._summary_lbl.configure(text=summary_text or self._default_summary())
        self._redraw()

    def set_message(self, message):
        self._points = []
        self._summary_lbl.configure(text=message)
        self._redraw()

    def _default_summary(self):
        if not self._points:
            return "No history yet."
        values = [value for _, value in self._points]
        latest = values[-1]
        return (
            f"Latest {self._fmt_value(latest)}  ·  "
            f"Min {self._fmt_value(min(values))}  ·  "
            f"Max {self._fmt_value(max(values))}"
        )

    def _fmt_value(self, value):
        if not isinstance(value, (int, float)):
            return "—"
        return f"{value:.1f}{self._unit}"

    def _downsample(self, points, max_points):
        if len(points) <= max_points:
            return list(points)
        bucket_size = len(points) / float(max_points)
        sampled = []
        for idx in range(max_points):
            start = int(idx * bucket_size)
            end = int((idx + 1) * bucket_size)
            bucket = points[start:max(start + 1, end)]
            if not bucket:
                continue
            avg_ts = int(sum(ts for ts, _ in bucket) / len(bucket))
            avg_value = sum(value for _, value in bucket) / len(bucket)
            sampled.append((avg_ts, avg_value))
        return sampled

    def _y_bounds(self, values):
        if self._max_value is not None:
            lo, hi = 0.0, float(self._max_value)
        else:
            lo = min(values)
            hi = max(values)
            if hi == lo:
                pad = 5.0 if hi == 0 else max(1.0, abs(hi) * 0.12)
                lo -= pad
                hi += pad
            else:
                pad = (hi - lo) * 0.15
                lo = max(0.0, lo - pad)
                hi += pad
        if hi <= lo:
            hi = lo + 1.0
        return lo, hi

    def _format_axis_value(self, value):
        if not isinstance(value, (int, float)):
            return "—"
        if self._unit == "%":
            return f"{value:.0f}%"
        return f"{value:.0f}{self._unit}"

    def _format_time_label(self, ts):
        try:
            now = time.time()
            if abs(now - ts) >= 172800:
                return time.strftime("%m/%d", time.localtime(ts))
            return time.strftime("%m/%d %H:%M", time.localtime(ts))
        except Exception:
            return "—"

    def _redraw(self):
        canvas = self._canvas
        width = max(canvas.winfo_width(), 40)
        height = max(canvas.winfo_height(), 80)
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, height, fill=CRUST, outline="")

        if not self._points:
            canvas.create_text(
                width / 2,
                height / 2,
                text="No history yet",
                fill=OVERLAY,
                font=("Segoe UI", 11),
            )
            return

        draw_points = self._downsample(self._points, max(24, min(320, width // 3)))
        values = [value for _, value in draw_points]
        lo, hi = self._y_bounds(values)

        left = 42
        right = 12
        top = 16
        bottom = 28
        plot_w = max(10, width - left - right)
        plot_h = max(10, height - top - bottom)

        for idx in range(4):
            frac = idx / 3.0
            y = top + plot_h * frac
            canvas.create_line(left, y, width - right, y, fill=SURFACE1, width=1)
            axis_value = hi - ((hi - lo) * frac)
            canvas.create_text(
                left - 6,
                y,
                text=self._format_axis_value(axis_value),
                fill=OVERLAY,
                font=("Segoe UI", 8),
                anchor="e",
            )

        if len(draw_points) == 1:
            x_vals = [left + plot_w / 2.0]
        else:
            step = plot_w / float(len(draw_points) - 1)
            x_vals = [left + (step * idx) for idx in range(len(draw_points))]

        coords = []
        for idx, (_, value) in enumerate(draw_points):
            x = x_vals[idx]
            y = top + ((hi - value) / (hi - lo)) * plot_h
            coords.extend([x, y])

        if len(coords) >= 4:
            canvas.create_line(*coords, fill=self._line_color, width=2, smooth=True, splinesteps=12)
        elif len(coords) == 2:
            canvas.create_oval(coords[0] - 2, coords[1] - 2, coords[0] + 2, coords[1] + 2,
                               fill=self._line_color, outline="")

        last_x, last_y = coords[-2], coords[-1]
        canvas.create_oval(last_x - 3, last_y - 3, last_x + 3, last_y + 3,
                           fill=self._line_color, outline="")
        canvas.create_line(left, top, left, top + plot_h, fill=SURFACE2, width=1)
        canvas.create_line(left, top + plot_h, width - right, top + plot_h, fill=SURFACE2, width=1)

        canvas.create_text(left, height - 10,
                           text=self._format_time_label(draw_points[0][0]),
                           fill=OVERLAY, font=("Segoe UI", 8), anchor="w")
        canvas.create_text(width - right, height - 10,
                           text=self._format_time_label(draw_points[-1][0]),
                           fill=OVERLAY, font=("Segoe UI", 8), anchor="e")


def _sep(parent, color=SURFACE1, height=1, pady=0):
    """Thin horizontal separator line."""
    tk.Frame(parent, bg=color, height=height).pack(fill="x", pady=pady)


# ── Main application ──────────────────────────────────────────────────────────

class RemoteServerConsole(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry(self._default_window_geometry())
        self.minsize(1280, 820)
        self.configure(bg=BASE)

        self.config_data = load_or_create_config()
        self.ssh = SSHClient(self.config_data)
        self.event_queue = queue.Queue()
        self.stats_job = None
        self.logs_job = None
        self.history_job = None
        self.command_counter = 0
        self.last_stats = {}

        self.host_var    = tk.StringVar(value=str(self.config_data["connection"].get("host", "")))
        self.user_var    = tk.StringVar(value=str(self.config_data["connection"].get("user", "")))
        self.port_var    = tk.StringVar(value=str(self.config_data["connection"].get("port", 22)))
        self.key_path_var = tk.StringVar(value=str(self.config_data["connection"].get("key_path", "")))
        self.timeout_var = tk.StringVar(value=str(self.config_data["connection"].get("connect_timeout_seconds", 8)))
        self.log_choice_var      = tk.StringVar()
        self.history_range_var   = tk.StringVar(value="7d")
        self.history_status_var  = tk.StringVar(value="Waiting for history...")
        self.conn_state_var      = tk.StringVar(value="Not connected")
        self.conn_detail_var     = tk.StringVar(value="Configure connection settings and click Connect.")
        self.conn_mini_var       = tk.StringVar(value="")

        self._configure_style()
        self._build_ui()
        self._load_log_choices()
        self._update_conn_mini()
        self.after(200, self._drain_queue)
        self.after(600, self.refresh_all)

    def _default_window_geometry(self):
        try:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
        except Exception:
            return "1440x900"
        w = max(1280, min(sw - 80, 1680))
        h = max(820,  min(sh - 80, 1080))
        return f"{w}x{h}+40+30"

    # ── ttk style — only used for Notebook tabs & Combobox ───────────────────
    def _configure_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        # Base ttk defaults
        style.configure(".", background=BASE, foreground=TEXT,
                        fieldbackground=SURFACE0, borderwidth=0)
        style.configure("TEntry",
                        relief="flat", borderwidth=1,
                        fieldbackground=CRUST,
                        foreground=TEXT, insertcolor=TEXT,
                        padding=(8, 6))
        style.configure("TCombobox",
                        fieldbackground=CRUST, foreground=TEXT,
                        selectbackground=SURFACE1, selectforeground=TEXT,
                        arrowcolor=SUBTEXT0, arrowsize=14, padding=(6, 5))
        style.map("TCombobox",
                  fieldbackground=[("readonly", CRUST)],
                  foreground=[("readonly", TEXT)])
        style.configure("TNotebook", background=MANTLE, borderwidth=0, tabmargins=0)
        style.configure("TNotebook.Tab",
                        background=SURFACE0, foreground=SUBTEXT0,
                        padding=(18, 8), font=("Segoe UI Semibold", 10),
                        borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", BLUE),  ("active", SURFACE1)],
                  foreground=[("selected", CRUST),  ("active", TEXT)])
        style.configure("Sash", background=SURFACE2, sashthickness=4)

    # ── Top-level layout ──────────────────────────────────────────────────────
    def _build_ui(self):
        self._build_header()
        _sep(self, SURFACE2, height=1)
        self._build_status_row()
        _sep(self, SURFACE1, height=1)
        self._build_metric_row()
        _sep(self, SURFACE2, height=1)

        # Main area: buttons left | logs right
        main = tk.Frame(self, bg=BASE)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=0, minsize=320)
        main.columnconfigure(1, weight=0, minsize=1)
        main.columnconfigure(2, weight=1)
        main.rowconfigure(0, weight=1)

        # Left: connection drawer + command buttons
        left_col = tk.Frame(main, bg=MANTLE)
        left_col.grid(row=0, column=0, sticky="nsew")
        self._build_commands_left(left_col)

        # Divider
        tk.Frame(main, bg=SURFACE2, width=1).grid(row=0, column=1, sticky="ns")

        # Right: logs
        right_col = tk.Frame(main, bg=MANTLE)
        right_col.grid(row=0, column=2, sticky="nsew")
        self._build_logs_right(right_col)

    # ── Header bar ────────────────────────────────────────────────────────────
    def _build_header(self):
        bar = tk.Frame(self, bg=MANTLE)
        bar.pack(fill="x")

        left = tk.Frame(bar, bg=MANTLE)
        left.pack(side="left", padx=(20, 0), pady=14)
        tk.Label(left, text="Sweet Shelves Console",
                 font=("Segoe UI Semibold", 18),
                 fg=TEXT, bg=MANTLE).pack(side="left")
        tk.Label(left, text="  ·  Remote server control",
                 font=("Segoe UI", 11),
                 fg=SUBTEXT0, bg=MANTLE).pack(side="left")

        right = tk.Frame(bar, bg=MANTLE)
        right.pack(side="right", padx=(0, 16), pady=10)

        self._conn_mini_lbl = tk.Label(right, textvariable=self.conn_mini_var,
                                       font=("Segoe UI", 9),
                                       fg=SUBTEXT0, bg=MANTLE)
        self._conn_mini_lbl.pack(side="left", padx=(0, 14))

        self._mk_btn(right, "⟳  Refresh", self.refresh_all,
                     bg=SURFACE1, fg=SAPPHIRE, font=("Segoe UI Semibold", 9)
                     ).pack(side="left", padx=(0, 6))
        self._mk_btn(right, "Open Config",
                     self.open_config_file,
                     bg=SURFACE0, fg=SUBTEXT0, font=("Segoe UI", 9)
                     ).pack(side="left")

        # ── Connection drawer (hidden by default) ──────────────────────────
        self._conn_drawer = tk.Frame(self, bg=MANTLE)
        # built lazily on first toggle
        self._conn_drawer_built = False
        self._conn_drawer_visible = False

    # ── Status row: Sweet Shelves tile + Server tile ──────────────────────────
    def _build_status_row(self):
        row = tk.Frame(self, bg=BASE)
        row.pack(fill="x", padx=0)
        row.columnconfigure(0, weight=1)
        row.columnconfigure(1, weight=1)

        self._ss_tile = StatusTile(row, "Sweet Shelves", icon="●")
        self._ss_tile.grid(row=0, column=0, sticky="nsew", padx=(0, 1))

        self._srv_tile = StatusTile(row, "Server", icon="●")
        self._srv_tile.grid(row=0, column=1, sticky="nsew")

    # ── Metric row: 5 compact cards ───────────────────────────────────────────
    def _build_metric_row(self):
        row = tk.Frame(self, bg=MANTLE)
        row.pack(fill="x")
        for i in range(5):
            row.columnconfigure(i, weight=1)

        self.cpu_card  = MetricCard(row, "CPU",         BLUE)
        self.mem_card  = MetricCard(row, "Memory",      GREEN)
        self.disk_card = MetricCard(row, "Disk",        YELLOW)
        self.usb_card  = MetricCard(row, "USB Backup",  MAUVE)
        self.temp_card = MetricCard(row, "Temperature", RED)

        sep_color = SURFACE2
        for idx, card in enumerate([self.cpu_card, self.mem_card,
                                     self.disk_card, self.usb_card, self.temp_card]):
            card.grid(row=0, column=idx, sticky="nsew")
            if idx < 4:
                tk.Frame(row, bg=sep_color, width=1).grid(
                    row=0, column=idx, sticky="nse")

    # ── Left panel: connection settings + command buttons ─────────────────────
    def _build_commands_left(self, parent):
        parent.rowconfigure(2, weight=1)
        parent.columnconfigure(0, weight=1)

        # Connection settings at top
        conn_frame = tk.Frame(parent, bg=MANTLE)
        conn_frame.grid(row=0, column=0, columnspan=2, sticky="ew")
        self._build_conn_inline(conn_frame)

        # Separator — must use grid here since parent uses grid
        tk.Frame(parent, bg=SURFACE2, height=1).grid(row=1, column=0, columnspan=2, sticky="ew")

        # Scrollable command area
        cmd_canvas = tk.Canvas(parent, bg=MANTLE, highlightthickness=0)
        cmd_canvas.grid(row=2, column=0, sticky="nsew")
        vsb = tk.Scrollbar(parent, orient="vertical", command=cmd_canvas.yview)
        vsb.grid(row=2, column=1, sticky="ns")
        cmd_canvas.configure(yscrollcommand=vsb.set)

        scroll_frame = tk.Frame(cmd_canvas, bg=MANTLE)
        win_id = cmd_canvas.create_window((0, 0), window=scroll_frame, anchor="nw")

        def _on_resize(e):
            cmd_canvas.itemconfigure(win_id, width=e.width)
        def _on_frame_change(e):
            cmd_canvas.configure(scrollregion=cmd_canvas.bbox("all"))

        cmd_canvas.bind("<Configure>", _on_resize)
        scroll_frame.bind("<Configure>", _on_frame_change)

        def _on_mousewheel(e):
            cmd_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        cmd_canvas.bind_all("<MouseWheel>", _on_mousewheel)

        self._populate_cmd_scroll(scroll_frame)

        self._cmd_scroll_frame = scroll_frame
        self._cmd_canvas = cmd_canvas

    def _populate_cmd_scroll(self, parent):
        """Render terminal pin → SS section → Server section into a scroll frame."""
        # ── Terminal access pinned at very top ─────────────────────────────
        terminal_cmd = next(
            (c for c in self.config_data.get("commands", [])
             if c.get("action") == "open_terminal"), None)
        pin = tk.Frame(parent, bg=MANTLE)
        pin.pack(fill="x", padx=12, pady=(10, 6))
        if terminal_cmd:
            self._mk_btn(
                pin,
                "⌨  " + terminal_cmd.get("label", "Open Server Terminal"),
                lambda c=terminal_cmd: self.execute_command(c),
                bg="#64748b", fg="#ffffff",
                font=("Segoe UI Semibold", 10), padx=14, pady=10, anchor="w",
            ).pack(fill="x")
        _sep(parent, SURFACE2)

        self._build_command_group_in(parent, "Sweet Shelves", "Sweet Shelves")
        _sep(parent, SURFACE2)
        self._build_command_group_in(parent, "Server", "Server")

    def _build_command_group_in(self, parent, title, section_key):
        hdr = tk.Frame(parent, bg=MANTLE)
        hdr.pack(fill="x")
        tk.Label(hdr, text=title.upper(),
                 font=("Segoe UI", 8, "bold"),
                 fg=SUBTEXT0, bg=MANTLE,
                 padx=16, pady=10).pack(side="left")

        btn_area = tk.Frame(parent, bg=MANTLE)
        btn_area.pack(fill="x", padx=12, pady=(0, 10))

        commands = [c for c in self.config_data.get("commands", [])
                    if self._command_section_name(c) == section_key
                    and c.get("action") != "open_terminal"]

        # Orange (accent) buttons float to top of each section
        commands.sort(key=lambda c: 0 if c.get("tone") == "accent" else 1)

        for cmd in commands:
            bg, fg = self._cmd_colors(cmd)
            self._mk_btn(
                btn_area, cmd.get("label", "?"),
                lambda c=cmd: self.execute_command(c),
                bg=bg, fg=fg,
                font=("Segoe UI Semibold", 10),
                padx=14, pady=8, anchor="w",
            ).pack(fill="x", pady=2)

    def _cmd_colors(self, cmd):
        """
        Simple, consistent scheme:
          orange  — restart / compound ops  (accent tone)
          red     — destructive             (danger tone)
          slate   — terminal access         (open_terminal action)
          blue    — everything else
        """
        if cmd.get("action") == "open_terminal":
            return ("#64748b", "#ffffff")
        tone = cmd.get("tone", "")
        label = cmd.get("label", "").lower()
        # Danger: reboot
        if tone == "danger" or "reboot" in label:
            return ("#dc2626", "#ffffff")
        # Orange: restart / compound ops
        if tone == "accent" or "restart" in label:
            return ("#dd6b20", "#ffffff")
        # Everything else: blue
        return ("#3b82f6", "#ffffff")

    # ── Inline connection panel (left column top) ─────────────────────────────
    def _build_conn_inline(self, parent):
        # Toggle-able — show/hide details
        self._conn_details_visible = False

        hdr = tk.Frame(parent, bg=MANTLE)
        hdr.pack(fill="x", padx=14, pady=(12, 8))
        tk.Label(hdr, text="CONNECTION",
                 font=("Segoe UI", 8, "bold"),
                 fg=SUBTEXT0, bg=MANTLE).pack(side="left")

        self._conn_toggle_btn = self._mk_btn(
            hdr, "▸ Edit",
            self._toggle_conn_details,
            bg=SURFACE0, fg=SAPPHIRE,
            font=("Segoe UI", 8), padx=8, pady=2)
        self._conn_toggle_btn.pack(side="right")

        # Mini status line (always visible)
        self._conn_mini_frame = tk.Frame(parent, bg=MANTLE)
        self._conn_mini_frame.pack(fill="x", padx=14, pady=(0, 10))
        self._conn_status_lbl = tk.Label(
            self._conn_mini_frame,
            textvariable=self.conn_state_var,
            font=("Segoe UI Semibold", 10), fg=SUBTEXT0, bg=MANTLE)
        self._conn_status_lbl.pack(side="left")
        tk.Label(self._conn_mini_frame,
                 textvariable=self.conn_mini_var,
                 font=("Segoe UI", 9), fg=OVERLAY, bg=MANTLE
                 ).pack(side="left", padx=(8, 0))

        # Expandable details
        self._conn_details = tk.Frame(parent, bg=MANTLE)
        # (hidden by default — only shown when toggled)

        body = self._conn_details
        fields = [
            ("Host",    self.host_var),
            ("User",    self.user_var),
            ("Port",    self.port_var),
            ("SSH Key", self.key_path_var),
            ("Timeout", self.timeout_var),
        ]
        for label, var in fields:
            tk.Label(body, text=label,
                     font=("Segoe UI", 9), fg=SUBTEXT0, bg=MANTLE
                     ).pack(anchor="w", padx=14, pady=(6, 1))
            row = tk.Frame(body, bg=MANTLE)
            row.pack(fill="x", padx=14)
            ttk.Entry(row, textvariable=var, width=22).pack(side="left", fill="x", expand=True)
            if label == "SSH Key":
                self._mk_btn(row, "Browse", self.browse_key,
                             bg=SURFACE0, fg=SUBTEXT1,
                             font=("Segoe UI", 8), padx=6, pady=3
                             ).pack(side="left", padx=(4, 0))

        _sep(body, SURFACE2, pady=(10, 0))
        btn_row = tk.Frame(body, bg=MANTLE)
        btn_row.pack(fill="x", padx=14, pady=(8, 12))
        self._mk_btn(btn_row, "Connect", self.test_connection,
                     bg="#2563eb", fg="#ffffff",
                     font=("Segoe UI Semibold", 10), padx=12, pady=7
                     ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._mk_btn(btn_row, "Save", self.save_settings,
                     bg=SURFACE1, fg=SUBTEXT1,
                     font=("Segoe UI", 10), padx=10, pady=7
                     ).pack(side="left")
        tk.Label(body, textvariable=self.conn_detail_var,
                 font=("Segoe UI", 8), fg=OVERLAY, bg=MANTLE,
                 wraplength=280, justify="left"
                 ).pack(anchor="w", padx=14, pady=(0, 8))

    def _toggle_conn_details(self):
        if self._conn_details_visible:
            self._conn_details.pack_forget()
            self._conn_details_visible = False
            self._conn_toggle_btn.configure(text="▸ Edit")
        else:
            self._conn_details.pack(fill="x")
            self._conn_details_visible = True
            self._conn_toggle_btn.configure(text="▴ Hide")

    # ── Right panel: logs ─────────────────────────────────────────────────────
    def _build_logs_right(self, parent):
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)

        notebook = ttk.Notebook(parent)
        notebook.grid(row=0, column=0, sticky="nsew")

        # Server logs tab
        log_tab = tk.Frame(notebook, bg=MANTLE)
        notebook.add(log_tab, text="  Server Logs  ")
        log_tab.columnconfigure(0, weight=1)
        log_tab.rowconfigure(1, weight=1)

        log_hdr = tk.Frame(log_tab, bg=MANTLE)
        log_hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 6))
        tk.Label(log_hdr, text="SOURCE",
                 font=("Segoe UI", 8, "bold"),
                 fg=SUBTEXT0, bg=MANTLE).pack(side="left", padx=(0, 10))
        self.log_combo = ttk.Combobox(log_hdr, textvariable=self.log_choice_var,
                                      state="readonly", width=30)
        self.log_combo.pack(side="left")
        self.log_combo.bind("<<ComboboxSelected>>", lambda _: self.refresh_logs())
        self._mk_btn(log_hdr, "⟳", self.refresh_logs,
                     bg=SURFACE0, fg=SAPPHIRE,
                     font=("Segoe UI", 9), padx=8, pady=3
                     ).pack(side="left", padx=(8, 0))

        self.log_text = tk.Text(
            log_tab,
            bg=CRUST, fg="#a6e3a1",
            insertbackground=TEXT,
            relief="flat", wrap="none",
            padx=14, pady=12,
            font=("Consolas", 9),
        )
        self.log_text.grid(row=1, column=0, sticky="nsew")
        sb = tk.Scrollbar(log_tab, orient="vertical", command=self.log_text.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.configure(state="disabled")

        # History tab
        hist_tab = tk.Frame(notebook, bg=MANTLE)
        notebook.add(hist_tab, text="  History  ")
        hist_tab.columnconfigure(0, weight=1)
        hist_tab.rowconfigure(1, weight=1)

        hist_hdr = tk.Frame(hist_tab, bg=MANTLE)
        hist_hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 6))
        tk.Label(hist_hdr, text="RANGE",
                 font=("Segoe UI", 8, "bold"),
                 fg=SUBTEXT0, bg=MANTLE).pack(side="left", padx=(0, 10))
        self.history_combo = ttk.Combobox(
            hist_hdr,
            textvariable=self.history_range_var,
            state="readonly",
            width=8,
            values=list(HISTORY_RANGE_OPTIONS.keys()),
        )
        self.history_combo.pack(side="left")
        self.history_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_history())
        self._mk_btn(hist_hdr, "⟳", self.refresh_history,
                     bg=SURFACE0, fg=SAPPHIRE,
                     font=("Segoe UI", 9), padx=8, pady=3
                     ).pack(side="left", padx=(8, 10))
        tk.Label(hist_hdr, textvariable=self.history_status_var,
                 font=("Segoe UI", 8),
                 fg=OVERLAY, bg=MANTLE).pack(side="left")

        charts = tk.Frame(hist_tab, bg=MANTLE)
        charts.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        charts.columnconfigure(0, weight=1)
        charts.columnconfigure(1, weight=1)
        charts.rowconfigure(0, weight=1)
        charts.rowconfigure(1, weight=1)

        self.cpu_history_chart = HistoryChart(charts, "CPU", BLUE, max_value=100, unit="%")
        self.cpu_history_chart.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        self.mem_history_chart = HistoryChart(charts, "Memory", GREEN, max_value=100, unit="%")
        self.mem_history_chart.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 6))
        self.disk_history_chart = HistoryChart(charts, "Disk", YELLOW, max_value=100, unit="%")
        self.disk_history_chart.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(6, 0))
        self.temp_history_chart = HistoryChart(charts, "Temperature", RED, unit="°C")
        self.temp_history_chart.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(6, 0))

        # Activity tab
        act_tab = tk.Frame(notebook, bg=MANTLE)
        notebook.add(act_tab, text="  Activity  ")
        act_tab.rowconfigure(0, weight=1)
        act_tab.columnconfigure(0, weight=1)

        self.activity_text = tk.Text(
            act_tab,
            bg=CRUST, fg=SUBTEXT1,
            insertbackground=TEXT,
            relief="flat", wrap="word",
            padx=14, pady=12,
            font=("Consolas", 9),
        )
        self.activity_text.grid(row=0, column=0, sticky="nsew")
        asb = tk.Scrollbar(act_tab, orient="vertical", command=self.activity_text.yview)
        asb.grid(row=0, column=1, sticky="ns")
        self.activity_text.configure(yscrollcommand=asb.set)
        self.activity_text.configure(state="disabled")

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _mk_btn(parent, text, command, *,
                bg=SURFACE1, fg=TEXT,
                font=("Segoe UI Semibold", 10),
                padx=14, pady=7, anchor="center", **kw):
        """Create a styled tk.Button with hover highlight."""
        hover = _brighten(bg, 28)
        btn = tk.Button(
            parent, text=text, command=command,
            bg=bg, fg=fg, activebackground=hover, activeforeground=fg,
            font=font, relief="flat", bd=0,
            padx=padx, pady=pady, cursor="hand2",
            anchor=anchor, **kw,
        )
        btn.bind("<Enter>", lambda _e, b=btn, h=hover: b.configure(bg=h))
        btn.bind("<Leave>", lambda _e, b=btn, n=bg:    b.configure(bg=n))
        return btn

    def render_command_buttons(self):
        """Rebuild command buttons when config changes."""
        for widget in self._cmd_scroll_frame.winfo_children():
            widget.destroy()
        self._populate_cmd_scroll(self._cmd_scroll_frame)

    def _update_conn_mini(self):
        user = self.user_var.get().strip()
        host = self.host_var.get().strip()
        state = self.conn_state_var.get().strip() or "Idle"
        addr = f"{user}@{host}".strip("@") or "—"
        self.conn_mini_var.set(f"{state}  ·  {addr}")

    def browse_key(self):
        filename = filedialog.askopenfilename(
            title="Choose SSH private key",
            filetypes=[("Private Keys", "*"), ("All Files", "*.*")],
        )
        if filename:
            self.key_path_var.set(filename)

    def open_config_file(self):
        path = config_path()
        path.touch(exist_ok=True)
        os.startfile(path)

    def collect_form_config(self):
        config = deep_copy_default_config()
        config["connection"] = {
            "host": self.host_var.get().strip(),
            "user": self.user_var.get().strip(),
            "port": int(self.port_var.get().strip() or "22"),
            "key_path": self.key_path_var.get().strip(),
            "connect_timeout_seconds": int(self.timeout_var.get().strip() or "8"),
        }
        config["commands"] = self.config_data.get("commands", [])
        config["logs"]     = self.config_data.get("logs", [])
        return config

    def save_settings(self):
        try:
            self.config_data = self.collect_form_config()
        except ValueError:
            messagebox.showerror("Invalid settings", "Port and timeout must be whole numbers.")
            return
        save_config(self.config_data)
        self.ssh.update_config(self.config_data)
        self._load_log_choices()
        self.render_command_buttons()
        self.conn_state_var.set("Saved")
        self.conn_detail_var.set(
            f"Using {self.config_data['connection']['user']}@"
            f"{self.config_data['connection']['host']}")
        self._update_conn_mini()
        self._append_activity("Saved connection settings.")

    def _load_log_choices(self):
        names = [item.get("name", "Unnamed log")
                 for item in self.config_data.get("logs", [])]
        self.log_combo["values"] = names
        if names and self.log_choice_var.get() not in names:
            self.log_choice_var.set(names[0])

    def _command_section_name(self, command):
        explicit = str(command.get("section", "")).strip()
        if explicit:
            return explicit
        label = str(command.get("label", "")).lower()
        raw   = str(command.get("command", "")).lower()
        if "sweetshelves" in label or "sweetshelves" in raw:
            return "Sweet Shelves"
        return "Server"

    # ── Workers & queue ───────────────────────────────────────────────────────
    def refresh_all(self):
        self.refresh_stats()
        self.refresh_logs()
        self.refresh_history()

    def refresh_stats(self):
        self._spawn_worker("stats_refresh", self._stats_worker)
        if self.stats_job:
            self.after_cancel(self.stats_job)
        self.stats_job = self.after(REFRESH_INTERVAL_MS, self.refresh_stats)

    def refresh_logs(self):
        self._spawn_worker("log_refresh", self._logs_worker)
        if self.logs_job:
            self.after_cancel(self.logs_job)
        self.logs_job = self.after(LOG_REFRESH_INTERVAL_MS, self.refresh_logs)

    def refresh_history(self):
        hours = self._selected_history_hours()
        self.history_status_var.set(f"Loading {self.history_range_var.get()} history...")
        self._spawn_worker("history_refresh", self._history_worker, hours)
        if self.history_job:
            self.after_cancel(self.history_job)
        self.history_job = self.after(HISTORY_REFRESH_INTERVAL_MS, self.refresh_history)

    def test_connection(self):
        self.save_settings()
        self.conn_state_var.set("Connecting…")
        self.conn_detail_var.set("Testing SSH connection…")
        self._update_conn_mini()
        self._append_activity("Testing SSH connection.")
        self._spawn_worker("test_connection", self._test_connection_worker)

    def execute_command(self, command_item):
        label = command_item.get("label", "Command")
        if command_item.get("action") == "open_terminal":
            self.open_server_terminal()
            return
        if command_item.get("confirm"):
            if not messagebox.askyesno("Confirm", f"Run '{label}' on the remote server?"):
                return
        self.command_counter += 1
        self._append_activity(f"Running '{label}'…")
        self._spawn_worker(f"command_{self.command_counter}", self._command_worker, command_item)

    def open_server_terminal(self):
        conn = self.config_data.get("connection", {})
        host = str(conn.get("host", "")).strip()
        user = str(conn.get("user", "")).strip()
        port = str(conn.get("port", 22)).strip()
        key  = str(conn.get("key_path", "")).strip()
        if not host or not user:
            messagebox.showerror("Missing settings", "Host and user are required.")
            return
        parts = ["ssh"]
        if key:
            parts.extend(["-i", key])
        if port and port != "22":
            parts.extend(["-p", port])
        parts.append(f"{user}@{host}")
        ssh_cmd = " ".join(f'"{p}"' if " " in p else p for p in parts)
        try:
            if sys.platform == "win32":
                wt = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "wt.exe"
                if wt.exists():
                    subprocess.Popen([str(wt), "new-tab", "powershell", "-NoExit", "-Command", ssh_cmd],
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                else:
                    subprocess.Popen(["powershell.exe", "-NoExit", "-Command", ssh_cmd],
                                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            else:
                subprocess.Popen(["x-terminal-emulator", "-e", ssh_cmd])
            self._append_activity(f"Opened terminal → {user}@{host}")
        except Exception as exc:
            messagebox.showerror("Terminal launch failed", str(exc))

    def _spawn_worker(self, job_type, target, payload=None):
        threading.Thread(target=target, args=(job_type, payload), daemon=True).start()

    def _test_connection_worker(self, job_type, _payload):
        try:
            result = self.ssh.run("printf connected", timeout=12)
            ok = result.returncode == 0 and "connected" in (result.stdout or "")
            self.event_queue.put({"type": job_type, "ok": ok,
                                  "error": (result.stderr or result.stdout or "").strip()})
        except Exception as exc:
            self.event_queue.put({"type": job_type, "ok": False, "error": str(exc)})

    def _stats_worker(self, job_type, _payload):
        try:
            result = self.ssh.run(REMOTE_STATS_SCRIPT, timeout=15)
            if result.returncode != 0:
                self.event_queue.put({"type": job_type, "ok": False,
                                      "error": (result.stderr or result.stdout).strip()})
                return
            stats = json.loads(result.stdout.strip())
            self.event_queue.put({"type": job_type, "ok": True, "stats": stats})
        except Exception as exc:
            self.event_queue.put({"type": job_type, "ok": False, "error": str(exc)})

    def _history_worker(self, job_type, hours):
        try:
            result = self.ssh.run(build_remote_history_script(hours), timeout=20)
            if result.returncode != 0:
                self.event_queue.put({
                    "type": job_type,
                    "ok": False,
                    "error": (result.stderr or result.stdout).strip(),
                })
                return
            history = json.loads(result.stdout.strip())
            self.event_queue.put({"type": job_type, "ok": True, "history": history})
        except Exception as exc:
            self.event_queue.put({"type": job_type, "ok": False, "error": str(exc)})

    def _logs_worker(self, job_type, _payload):
        name = self.log_choice_var.get().strip()
        chosen = next((l for l in self.config_data.get("logs", [])
                       if l.get("name") == name), None)
        if chosen is None and self.config_data.get("logs"):
            chosen = self.config_data["logs"][0]
        if chosen is None:
            self.event_queue.put({"type": job_type, "ok": True,
                                  "log_name": "No logs", "content": "No logs configured."})
            return
        try:
            result = self.ssh.run(chosen.get("command", "echo No log command configured"), timeout=15)
            if result.returncode != 0:
                self.event_queue.put({"type": job_type, "ok": False,
                                      "log_name": chosen.get("name", "Log"),
                                      "error": (result.stderr or result.stdout).strip()})
                return
            self.event_queue.put({"type": job_type, "ok": True,
                                  "log_name": chosen.get("name", "Log"),
                                  "content": result.stdout.strip() or "(No output)"})
        except Exception as exc:
            self.event_queue.put({"type": job_type, "ok": False,
                                  "log_name": chosen.get("name", "Log"), "error": str(exc)})

    def _command_worker(self, job_type, command_item):
        label   = command_item.get("label", "Command")
        command = command_item.get("command", "")
        try:
            result = self.ssh.run(command, timeout=90)
            self.event_queue.put({
                "type": job_type, "ok": result.returncode == 0,
                "label": label,
                "output": (result.stdout or "").strip(),
                "error":  (result.stderr or "").strip(),
                "code":   result.returncode,
            })
        except Exception as exc:
            self.event_queue.put({"type": job_type, "ok": False,
                                  "label": label, "error": str(exc), "code": -1})

    def _drain_queue(self):
        while True:
            try:
                self._handle_event(self.event_queue.get_nowait())
            except queue.Empty:
                break
        self.after(200, self._drain_queue)

    def _handle_event(self, message):
        t = message.get("type", "")
        if t == "test_connection":
            self._handle_connection_test(message)
        elif t == "stats_refresh":
            self._handle_stats_update(message)
        elif t == "history_refresh":
            self._handle_history_update(message)
        elif t == "log_refresh":
            self._handle_logs_update(message)
        elif t.startswith("command_"):
            self._handle_command_result(message)

    def _handle_connection_test(self, message):
        conn = self.config_data["connection"]
        if message.get("ok"):
            self.conn_state_var.set("Connected")
            self.conn_detail_var.set(
                f"SSH confirmed  ·  {conn['user']}@{conn['host']}")
            self._conn_status_lbl.configure(fg=GREEN)
            self._update_conn_mini()
            self._append_activity("SSH connection verified.")
            self.refresh_all()
        else:
            self.conn_state_var.set("Failed")
            self.conn_detail_var.set(message.get("error", "Unknown SSH error"))
            self._conn_status_lbl.configure(fg=RED)
            self._update_conn_mini()
            self._append_activity(f"SSH failed: {message.get('error', 'Unknown error')}")

    def _handle_stats_update(self, message):
        if not message.get("ok"):
            self._srv_tile.set_waiting()
            self.conn_state_var.set("Stats unavailable")
            self.conn_detail_var.set(message.get("error", "Unable to read stats."))
            self._update_conn_mini()
            self._append_activity(f"Stats failed: {message.get('error', '?')}")
            return

        stats = message["stats"]
        self.last_stats = stats

        # ── Server status tile ──────────────────────────────────────────────
        hostname = stats.get("hostname", "server")
        uptime   = stats.get("uptime", "?")
        load     = stats.get("load_average") or []
        load_txt = " / ".join(str(v) for v in load[:3]) if load else "—"
        self._srv_tile.set_live(
            "ONLINE", GREEN,
            f"{hostname}  ·  Up {uptime}  ·  Load {load_txt}",
            GREEN,
        )

        # ── Sweet Shelves service tile ──────────────────────────────────────
        svc = stats.get("service", {})
        git = stats.get("git", {})
        if svc.get("active"):
            up = svc.get("uptime") or "running"
            git_when    = git.get("when") or ""
            git_subject = git.get("subject") or ""
            if git_when and git_subject:
                detail = f"Up {up}  ·  pulled {git_when}  —  {git_subject}"
            else:
                detail = f"Up {up}"
            self._ss_tile.set_live("LIVE", GREEN, detail, GREEN)
        else:
            self._ss_tile.set_live("DOWN", RED, "sweetshelves.service is not running", RED)

        # Connection mini status
        self.conn_state_var.set("Live")
        self._update_conn_mini()

        # ── Metric cards ────────────────────────────────────────────────────
        cpu = stats.get("cpu_percent")
        self.cpu_card.set_value(cpu, self._pct_txt(cpu), f"Load {load_txt}")

        mem = stats.get("memory", {})
        self.mem_card.set_value(
            mem.get("percent"),
            self._pct_txt(mem.get("percent")),
            f"{mem.get('used_mb', '—')} MB / {mem.get('total_mb', '—')} MB",
        )

        dsk = stats.get("disk", {})
        self.disk_card.set_value(
            dsk.get("percent"),
            self._pct_txt(dsk.get("percent")),
            f"{dsk.get('used_gb', '—')} / {dsk.get('total_gb', '—')} GB",
        )

        usb = stats.get("usb", {})
        usb_val = f"{usb.get('free_gb', '—')} GB free" if usb.get("free_gb") is not None else "No USB"
        self.usb_card.set_value(
            usb.get("percent"),
            usb_val,
            usb.get("path") or "No USB found",
        )

        tmp = stats.get("temperature_c")
        if isinstance(tmp, (int, float)):
            tmp_pct = min(max((tmp / 90.0) * 100.0, 0.0), 100.0)
            tmp_val = f"{tmp:.1f} °C"
        else:
            tmp_pct = None
            tmp_val = "—"
        self.temp_card.set_value(tmp_pct, tmp_val, self._temp_label(tmp))

    def _handle_history_update(self, message):
        charts = (
            self.cpu_history_chart,
            self.mem_history_chart,
            self.disk_history_chart,
            self.temp_history_chart,
        )
        if not message.get("ok"):
            error = message.get("error", "Unable to load history.")
            self.history_status_var.set("History unavailable")
            for chart in charts:
                chart.set_message(error)
            return

        history = message.get("history", {}) or {}
        status = history.get("status", "ok")
        points = history.get("points", []) or []
        latest = history.get("latest") or {}

        if status == "missing_db":
            text = "No history DB yet. Wait for the Pi sampler to write its first snapshot."
            self.history_status_var.set("No history yet")
            for chart in charts:
                chart.set_message(text)
            return
        if status == "missing_table":
            text = "History storage exists, but the snapshot table is not ready yet."
            self.history_status_var.set("History initializing")
            for chart in charts:
                chart.set_message(text)
            return
        if status == "error":
            error = history.get("error", "Unknown history query error.")
            self.history_status_var.set("History query failed")
            for chart in charts:
                chart.set_message(error)
            return
        if not points:
            text = "No samples were found for this time range yet."
            self.history_status_var.set(f"No {self.history_range_var.get()} samples")
            for chart in charts:
                chart.set_message(text)
            return

        latest_ts = latest.get("collected_ts")
        latest_label = self._format_history_time(latest_ts)
        self.history_status_var.set(
            f"{len(points)} samples  ·  {self.history_range_var.get()}  ·  latest {latest_label}"
        )

        def _metric_points(key):
            out = []
            for row in points:
                ts = row.get("collected_ts")
                value = row.get(key)
                if isinstance(ts, (int, float)) and isinstance(value, (int, float)):
                    out.append((int(ts), float(value)))
            return out

        self.cpu_history_chart.set_points(_metric_points("cpu_percent"))
        self.mem_history_chart.set_points(_metric_points("memory_percent"))
        self.disk_history_chart.set_points(_metric_points("disk_percent"))
        self.temp_history_chart.set_points(_metric_points("temp_c"))

    def _handle_logs_update(self, message):
        name = message.get("log_name", "Log")
        if not message.get("ok"):
            self._set_text(self.log_text, f"[{name}]\n\n{message.get('error', 'Unable to read.')}")
            return
        self._set_text(self.log_text, f"[{name}]\n\n{message.get('content', '')}")

    def _handle_command_result(self, message):
        label = message.get("label", "Command")
        if message.get("ok"):
            out     = message.get("output", "")
            snippet = out[:900] + ("\n…" if len(out) > 900 else "")
            body    = f"{label} completed."
            if snippet:
                body += f"\n\n{snippet}"
            self._append_activity(body)
        else:
            err = message.get("error") or message.get("output") or "Command failed."
            self._append_activity(f"{label} failed (exit {message.get('code', '?')}):\n{err}")
        if any(kw in label.lower() for kw in ("status", "restart", "reboot")):
            self.after(1200, self.refresh_all)

    def _append_activity(self, text):
        ts = time.strftime("%H:%M:%S")
        self.activity_text.configure(state="normal")
        self.activity_text.insert("end", f"[{ts}]  {text}\n\n")
        self.activity_text.see("end")
        self.activity_text.configure(state="disabled")

    def _selected_history_hours(self):
        return HISTORY_RANGE_OPTIONS.get(self.history_range_var.get(), 168)

    def _format_history_time(self, ts):
        try:
            return time.strftime("%m/%d %H:%M", time.localtime(int(ts)))
        except Exception:
            return "—"

    def _set_text(self, widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _pct_txt(self, v):
        return f"{v:.1f}%" if isinstance(v, (int, float)) else "—"

    def _temp_label(self, t):
        if not isinstance(t, (int, float)):
            return "No sensor data"
        if t >= 80:
            return "Hot — check cooling"
        if t >= 65:
            return "Warm — elevated"
        return "Normal range"


def _brighten(hex_color, amount=20):
    """Lighten a hex color for hover state."""
    try:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        r = min(255, r + amount)
        g = min(255, g + amount)
        b = min(255, b + amount)
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return hex_color


def main():
    enable_windows_dpi_awareness()
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--self-test", action="store_true",
                        help="Validate config creation and exit.")
    args = parser.parse_args()

    if args.self_test:
        config = load_or_create_config()
        print(json.dumps({"config_path": str(config_path()),
                          "host": config["connection"]["host"]}))
        return

    app = RemoteServerConsole()
    app.mainloop()


if __name__ == "__main__":
    main()
