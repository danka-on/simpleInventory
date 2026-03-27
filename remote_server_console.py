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
DEFAULT_KEY_PATH = str(Path.home() / ".ssh" / "sweet_shelves_pi")

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
    "timestamp": int(time.time()),
}

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


class MetricCard(ttk.Frame):
    def __init__(self, master, title, color, *, compact=False, canvas_width=210):
        super().__init__(master, style="Card.TFrame", padding=(14, 14) if compact else (18, 16))
        self.color = color
        self.title_var = tk.StringVar(value=title)
        self.value_var = tk.StringVar(value="--")
        self.subtitle_var = tk.StringVar(value="Waiting for data")
        value_style = "CompactMetricValue.TLabel" if compact else "MetricValue.TLabel"
        body_style = "CompactCardBody.TLabel" if compact else "CardBody.TLabel"

        ttk.Label(self, textvariable=self.title_var, style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(self, textvariable=self.value_var, style=value_style).pack(anchor="w", pady=(6, 6))
        self.canvas_width = canvas_width
        self.canvas = tk.Canvas(self, width=self.canvas_width, height=22, bg="#0d1829", highlightthickness=0)
        self.canvas.pack(fill="x", pady=(0, 8))
        ttk.Label(self, textvariable=self.subtitle_var, style=body_style, wraplength=160 if compact else 230, justify="left").pack(anchor="w")
        self.set_value(None, "--", "Waiting for data")

    def set_value(self, percent, value_text, subtitle_text):
        self.value_var.set(value_text)
        self.subtitle_var.set(subtitle_text)
        self.canvas.delete("all")
        r = 5
        w, h = self.canvas_width, 22
        # Track
        self.canvas.create_rectangle(r, 2, w - r, h - 2, fill="#14213a", outline="")
        self.canvas.create_oval(0, 2, r * 2, h - 2, fill="#14213a", outline="")
        self.canvas.create_oval(w - r * 2, 2, w, h - 2, fill="#14213a", outline="")
        fill_width = 0
        if isinstance(percent, (int, float)):
            percent = max(0.0, min(100.0, float(percent)))
            fill_width = int((w - 2) * (percent / 100.0))
        if fill_width > r:
            self.canvas.create_rectangle(r, 2, fill_width, h - 2, fill=self.color, outline="")
            self.canvas.create_oval(0, 2, r * 2, h - 2, fill=self.color, outline="")


class ServiceCard(ttk.Frame):
    """Status card for Sweet Shelves service — shows LIVE / DOWN with uptime."""
    _LIVE_FG  = "#4ade80"
    _DOWN_FG  = "#f87171"
    _WAIT_FG  = "#94a3b8"
    _CARD_BG  = "#0d1829"

    def __init__(self, master):
        super().__init__(master, style="Card.TFrame", padding=(18, 16))
        ttk.Label(self, text="Sweet Shelves", style="CardTitle.TLabel").pack(anchor="w")
        self._dot_label = tk.Label(
            self, text="--",
            font=("Segoe UI Semibold", 22),
            bg=self._CARD_BG, fg=self._WAIT_FG,
        )
        self._dot_label.pack(anchor="w", pady=(6, 4))
        # thin accent bar (canvas mimicking MetricCard)
        self._canvas = tk.Canvas(self, width=180, height=22, bg=self._CARD_BG, highlightthickness=0)
        self._canvas.pack(fill="x", pady=(0, 8))
        self._sub_label = tk.Label(
            self, text="Waiting for data",
            font=("Segoe UI", 9),
            bg=self._CARD_BG, fg="#94a3b8",
            wraplength=180, justify="left",
        )
        self._sub_label.pack(anchor="w")
        self._draw_bar(0, self._WAIT_FG)

    def _draw_bar(self, fill_frac, color):
        w, h, r = 180, 22, 5
        self._canvas.delete("all")
        self._canvas.create_rectangle(r, 2, w - r, h - 2, fill="#14213a", outline="")
        self._canvas.create_oval(0, 2, r * 2, h - 2, fill="#14213a", outline="")
        self._canvas.create_oval(w - r * 2, 2, w, h - 2, fill="#14213a", outline="")
        fw = int((w - 2) * fill_frac)
        if fw > r:
            self._canvas.create_rectangle(r, 2, fw, h - 2, fill=color, outline="")
            self._canvas.create_oval(0, 2, r * 2, h - 2, fill=color, outline="")

    def set_status(self, active, uptime=None):
        if active:
            self._dot_label.configure(text="● LIVE", fg=self._LIVE_FG)
            sub = f"Up {uptime}" if uptime else "Running"
            self._draw_bar(1.0, self._LIVE_FG)
        else:
            self._dot_label.configure(text="● DOWN", fg=self._DOWN_FG)
            sub = "Service not running"
            self._draw_bar(0.0, self._DOWN_FG)
        self._sub_label.configure(text=sub)


class RemoteServerConsole(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry(self._default_window_geometry())
        self.minsize(1280, 900)
        self.configure(bg="#060c1a")

        self.config_data = load_or_create_config()
        self.ssh = SSHClient(self.config_data)
        self.event_queue = queue.Queue()
        self.stats_job = None
        self.logs_job = None
        self.command_counter = 0
        self.last_stats = {}
        self.connection_collapsed = False
        self.backup_helper_visible = False

        self.host_var = tk.StringVar(value=str(self.config_data["connection"].get("host", "")))
        self.user_var = tk.StringVar(value=str(self.config_data["connection"].get("user", "")))
        self.port_var = tk.StringVar(value=str(self.config_data["connection"].get("port", 22)))
        self.key_path_var = tk.StringVar(value=str(self.config_data["connection"].get("key_path", "")))
        self.timeout_var = tk.StringVar(value=str(self.config_data["connection"].get("connect_timeout_seconds", 8)))
        self.log_choice_var = tk.StringVar()
        self.connection_state_var = tk.StringVar(value="Idle")
        self.connection_detail_var = tk.StringVar(value="Save your connection settings, then test the server.")
        self.connection_mini_var = tk.StringVar(value="Connection panel open")
        self.server_identity_var = tk.StringVar(value="No connection yet")
        self.summary_var = tk.StringVar(value="Live CPU, temperature, memory, disk, and server logs in one place.")

        self._configure_style()
        self._build_ui()
        self._load_log_choices()
        self._update_connection_mini_status()
        self.after(200, self._drain_queue)
        self.after(600, self.refresh_all)

    def _default_window_geometry(self):
        try:
            screen_width = self.winfo_screenwidth()
            screen_height = self.winfo_screenheight()
        except Exception:
            return "1680x1160"

        width = max(1600, min(screen_width - 120, 1760))
        height = max(1120, min(screen_height - 100, 1220))
        return f"{width}x{height}+20+20"

    def _configure_style(self):
        BG       = "#060c1a"
        PANEL    = "#0d1829"
        HERO     = "#091428"
        INK      = "#f1f5f9"
        INK_MUT  = "#94a3b8"
        INK_DIM  = "#64748b"
        ACCENT   = "#3b82f6"
        TRACK    = "#14213a"

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=INK, fieldbackground=PANEL)
        style.configure("App.TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Card.TFrame", background=PANEL, relief="flat")
        style.configure("Hero.TFrame", background=HERO)
        style.configure("Title.TLabel", background=BG, foreground="#ffffff", font=("Segoe UI Semibold", 26))
        style.configure("SectionTitle.TLabel", background=PANEL, foreground="#60a5fa", font=("Segoe UI Semibold", 13))
        style.configure("Muted.TLabel", background=BG, foreground=INK_MUT, font=("Segoe UI", 10))
        style.configure("HeroTitle.TLabel", background=HERO, foreground=INK, font=("Segoe UI Semibold", 17))
        style.configure("HeroBody.TLabel", background=HERO, foreground=INK_MUT, font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background=PANEL, foreground="#7dd3fc", font=("Segoe UI Semibold", 10))
        style.configure("CardBody.TLabel", background=PANEL, foreground=INK_MUT, font=("Segoe UI", 9))
        style.configure("CompactCardBody.TLabel", background=PANEL, foreground=INK_MUT, font=("Segoe UI", 8))
        style.configure("MetricValue.TLabel", background=PANEL, foreground=INK, font=("Segoe UI Semibold", 24))
        style.configure("CompactMetricValue.TLabel", background=PANEL, foreground=INK, font=("Segoe UI Semibold", 16))
        style.configure("FieldLabel.TLabel", background=PANEL, foreground=INK_MUT, font=("Segoe UI", 9))
        style.configure("TButton", font=("Segoe UI Semibold", 10), padding=(13, 10), borderwidth=0)
        style.map("TButton", foreground=[("disabled", INK_DIM)])
        style.configure("Primary.TButton", background="#2563eb", foreground="#ffffff")
        style.map("Primary.TButton", background=[("active", "#1d4ed8")])
        style.configure("Secondary.TButton", background="#1e2d4a", foreground="#cbd5e1")
        style.map("Secondary.TButton", background=[("active", "#263657")])
        style.configure("Danger.TButton", background="#dc2626", foreground="#ffffff")
        style.map("Danger.TButton", background=[("active", "#b91c1c")])
        style.configure("Accent.TButton", background="#0284c7", foreground="#ffffff")
        style.map("Accent.TButton", background=[("active", "#0369a1")])
        style.configure("Success.TButton", background="#16a34a", foreground="#ffffff")
        style.map("Success.TButton", background=[("active", "#15803d")])
        style.configure("Warning.TButton", background="#ea580c", foreground="#ffffff")
        style.map("Warning.TButton", background=[("active", "#c2410c")])
        style.configure("Info.TButton", background="#0891b2", foreground="#ffffff")
        style.map("Info.TButton", background=[("active", "#0e7490")])
        style.configure("TEntry", relief="flat", fieldbackground="#0a1020", foreground=INK, insertcolor=INK)
        style.configure("TCombobox", fieldbackground="#0a1020", foreground=INK, arrowsize=15,
                        selectbackground=PANEL, selectforeground=INK)
        style.map("TCombobox", fieldbackground=[("readonly", "#0a1020")], foreground=[("readonly", INK)])
        style.configure("TNotebook", background=PANEL, borderwidth=0)
        style.configure("TNotebook.Tab", background="#0a1325", foreground=INK_DIM,
                        padding=(16, 9), font=("Segoe UI Semibold", 10))
        style.map(
            "TNotebook.Tab",
            background=[("selected", ACCENT), ("active", "#1e3a5f")],
            foreground=[("selected", "#ffffff"), ("active", INK_MUT)],
        )
        self._TRACK_COLOR = TRACK
        self._PANEL_COLOR = PANEL
        self._HERO_COLOR  = HERO

    def _build_ui(self):
        outer = ttk.Frame(self, style="App.TFrame", padding=20)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=0)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(3, weight=4)
        outer.rowconfigure(4, weight=1)

        ttk.Label(outer, text=APP_TITLE, style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(outer, text="Desktop control center for your remote Linux server.", style="Muted.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 18)
        )

        left = ttk.Frame(outer, style="Panel.TFrame", padding=18)
        left.grid(row=2, column=0, sticky="nw", padx=(0, 18))
        left.columnconfigure(0, weight=1)

        top_right = ttk.Frame(outer, style="App.TFrame")
        top_right.grid(row=2, column=1, sticky="new")
        top_right.columnconfigure(0, weight=1)

        self._build_connection_panel(left)
        self._build_hero(top_right)
        self._build_metric_grid(top_right)
        self._build_commands_panel(outer)
        self._build_logs_panel(outer)

    def _build_connection_panel(self, parent):
        header = ttk.Frame(parent, style="Panel.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Connection", style="SectionTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.connection_toggle_button = ttk.Button(
            header,
            text="Hide",
            command=self.toggle_connection_panel,
            style="Secondary.TButton",
        )
        self.connection_toggle_button.grid(row=0, column=1, sticky="e")

        ttk.Label(parent, textvariable=self.connection_mini_var, style="CardBody.TLabel", wraplength=240, justify="left").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )

        self.connection_details_frame = ttk.Frame(parent, style="Panel.TFrame")
        self.connection_details_frame.grid(row=2, column=0, columnspan=2, sticky="nsew")
        self.connection_details_frame.columnconfigure(0, weight=1)

        fields = [
            ("Host", self.host_var),
            ("User", self.user_var),
            ("Port", self.port_var),
            ("SSH Key", self.key_path_var),
            ("Timeout", self.timeout_var),
        ]
        for idx, (label, variable) in enumerate(fields, start=1):
            ttk.Label(self.connection_details_frame, text=label, style="FieldLabel.TLabel").grid(
                row=idx * 2 - 1, column=0, sticky="w", pady=(12, 4)
            )
            entry = ttk.Entry(self.connection_details_frame, textvariable=variable, width=28)
            entry.grid(row=idx * 2, column=0, sticky="ew")
            if label == "SSH Key":
                ttk.Button(self.connection_details_frame, text="Browse", command=self.browse_key, style="Secondary.TButton").grid(
                    row=idx * 2, column=1, padx=(8, 0), sticky="ew"
                )

        button_row = ttk.Frame(self.connection_details_frame, style="Panel.TFrame")
        button_row.grid(row=12, column=0, columnspan=2, sticky="ew", pady=(18, 10))
        button_row.columnconfigure(0, weight=1)
        button_row.columnconfigure(1, weight=1)
        ttk.Button(button_row, text="Save Settings", command=self.save_settings, style="Primary.TButton").grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )
        ttk.Button(button_row, text="Test Connection", command=self.test_connection, style="Secondary.TButton").grid(
            row=0, column=1, sticky="ew", padx=(6, 0)
        )

        ttk.Button(self.connection_details_frame, text="Refresh Now", command=self.refresh_all, style="Accent.TButton").grid(
            row=13, column=0, columnspan=2, sticky="ew"
        )

        status_card = ttk.Frame(self.connection_details_frame, style="Hero.TFrame", padding=14)
        status_card.grid(row=14, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        ttk.Label(status_card, text="Connection Status", style="HeroTitle.TLabel").pack(anchor="w")
        ttk.Label(status_card, textvariable=self.connection_state_var, style="HeroTitle.TLabel").pack(anchor="w", pady=(8, 2))
        ttk.Label(status_card, textvariable=self.connection_detail_var, style="HeroBody.TLabel", wraplength=240, justify="left").pack(anchor="w")


    def _build_hero(self, parent):
        hero = ttk.Frame(parent, style="Hero.TFrame", padding=18)
        hero.grid(row=0, column=0, sticky="ew")
        hero.columnconfigure(0, weight=1)
        hero.columnconfigure(1, weight=1)
        ttk.Label(hero, textvariable=self.server_identity_var, style="HeroTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(hero, textvariable=self.summary_var, style="HeroBody.TLabel", wraplength=720, justify="left").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Button(hero, text="Open Config File", command=self.open_config_file, style="Secondary.TButton").grid(
            row=0, column=1, sticky="e"
        )

    def _build_metric_grid(self, parent):
        metrics = ttk.Frame(parent, style="App.TFrame")
        metrics.grid(row=1, column=0, sticky="ew", pady=(14, 12))
        for index in range(6):
            metrics.columnconfigure(index, weight=1)

        self.cpu_card = MetricCard(metrics, "CPU", "#38bdf8")
        self.cpu_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.memory_card = MetricCard(metrics, "Memory", "#34d399")
        self.memory_card.grid(row=0, column=1, sticky="nsew", padx=(0, 10))
        self.disk_card = MetricCard(metrics, "Disk", "#f59e0b")
        self.disk_card.grid(row=0, column=2, sticky="nsew", padx=(0, 10))
        self.usb_card = MetricCard(metrics, "USB", "#a78bfa")
        self.usb_card.grid(row=0, column=3, sticky="nsew", padx=(0, 10))
        self.temp_card = MetricCard(metrics, "Temperature", "#fb7185")
        self.temp_card.grid(row=0, column=4, sticky="nsew", padx=(0, 10))
        self.service_card = ServiceCard(metrics)
        self.service_card.grid(row=0, column=5, sticky="nsew")

    def _build_commands_panel(self, parent):
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=18)
        panel.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(0, 12))
        panel.columnconfigure(0, weight=1)
        panel.columnconfigure(1, weight=1)
        panel.rowconfigure(1, weight=1)
        ttk.Label(panel, text="Command Deck", style="SectionTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")

        self.command_buttons_parent = panel
        self.render_command_buttons()

    def _build_logs_panel(self, parent):
        panel = ttk.Frame(parent, style="Panel.TFrame", padding=14)
        panel.grid(row=4, column=0, columnspan=2, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(panel)
        notebook.grid(row=0, column=0, sticky="nsew")

        server_logs_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=12)
        server_logs_tab.columnconfigure(0, weight=0)
        server_logs_tab.columnconfigure(1, weight=1)
        server_logs_tab.rowconfigure(1, weight=1)
        notebook.add(server_logs_tab, text="Server Logs")

        activity_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=12)
        activity_tab.columnconfigure(0, weight=1)
        activity_tab.rowconfigure(0, weight=1)
        notebook.add(activity_tab, text="Activity Log")

        ttk.Label(server_logs_tab, text="Server Logs", style="SectionTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.log_combo = ttk.Combobox(server_logs_tab, textvariable=self.log_choice_var, state="readonly", width=32)
        self.log_combo.grid(row=0, column=1, sticky="e", padx=(10, 0))
        self.log_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_logs())

        self.log_text = tk.Text(
            server_logs_tab,
            height=8,
            background="#cbd5e1",
            foreground="#020617",
            insertbackground="#020617",
            relief="flat",
            wrap="none",
            padx=12,
            pady=12,
        )
        self.log_text.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(14, 0))
        self.log_text.configure(state="disabled")

        self.activity_text = tk.Text(
            activity_tab,
            height=8,
            background="#cbd5e1",
            foreground="#020617",
            insertbackground="#020617",
            relief="flat",
            wrap="word",
            padx=12,
            pady=12,
        )
        self.activity_text.grid(row=0, column=0, sticky="nsew")
        self.activity_text.configure(state="disabled")

    def browse_key(self):
        filename = filedialog.askopenfilename(
            title="Choose SSH private key",
            filetypes=[("Private Keys", "*"), ("All Files", "*.*")],
        )
        if filename:
            self.key_path_var.set(filename)

    def toggle_connection_panel(self):
        self.set_connection_panel_collapsed(not self.connection_collapsed)

    def set_connection_panel_collapsed(self, collapsed):
        self.connection_collapsed = collapsed
        if collapsed:
            self.connection_details_frame.grid_remove()
            self.connection_toggle_button.configure(text="Show")
        else:
            self.connection_details_frame.grid()
            self.connection_toggle_button.configure(text="Hide")
        self._update_connection_mini_status()

    def _update_connection_mini_status(self):
        connection = f"{self.user_var.get().strip()}@{self.host_var.get().strip()}".strip("@")
        state = self.connection_state_var.get().strip() or "Idle"
        if self.connection_collapsed:
            self.connection_mini_var.set(f"{state} | {connection}")
        else:
            self.connection_mini_var.set(f"{state} | {connection} | Connection details open")

    def open_config_file(self):
        path = config_path()
        path.touch(exist_ok=True)
        os.startfile(path)

    def collect_form_config(self):
        port_value = int(self.port_var.get().strip() or "22")
        timeout_value = int(self.timeout_var.get().strip() or "8")
        config = deep_copy_default_config()
        config["connection"] = {
            "host": self.host_var.get().strip(),
            "user": self.user_var.get().strip(),
            "port": port_value,
            "key_path": self.key_path_var.get().strip(),
            "connect_timeout_seconds": timeout_value,
        }
        config["commands"] = self.config_data.get("commands", [])
        config["logs"] = self.config_data.get("logs", [])
        return config

    def save_settings(self):
        try:
            self.config_data = self.collect_form_config()
        except ValueError:
            messagebox.showerror("Invalid settings", "Port and timeout need to be whole numbers.")
            return

        save_config(self.config_data)
        self.ssh.update_config(self.config_data)
        self._load_log_choices()
        self.render_command_buttons()
        self.connection_state_var.set("Saved")
        self.connection_detail_var.set(f"Using {self.config_data['connection']['user']}@{self.config_data['connection']['host']}")
        self._update_connection_mini_status()
        self._append_activity("Saved connection settings.")

    def _load_log_choices(self):
        log_names = [item.get("name", "Unnamed log") for item in self.config_data.get("logs", [])]
        self.log_combo["values"] = log_names
        if log_names and self.log_choice_var.get() not in log_names:
            self.log_choice_var.set(log_names[0])

    def render_command_buttons(self):
        for child in self.command_buttons_parent.winfo_children():
            info = child.grid_info()
            if info.get("row") == 0:
                continue
            child.destroy()

        section_map = {"Sweet Shelves": [], "Server": []}
        for command in self.config_data.get("commands", []):
            section_map.setdefault(self._command_section_name(command), []).append(command)

        for index, section_name in enumerate(["Sweet Shelves", "Server"]):
            section_commands = section_map.get(section_name, [])
            section = ttk.LabelFrame(self.command_buttons_parent, text=section_name, padding=10)
            section.grid(row=1, column=index, sticky="nsew", padx=(0, 12 if index == 0 else 0), pady=(14, 0))
            columns = 3 if section_name == "Sweet Shelves" else 2
            for column in range(columns):
                section.columnconfigure(column, weight=1)

            for button_index, command in enumerate(section_commands):
                grid_row = button_index // columns
                grid_col = button_index % columns
                style_name = self._command_style_name(command)
                ttk.Button(
                    section,
                    text=command.get("label", "Unnamed"),
                    style=style_name,
                    command=lambda command_item=command: self.execute_command(command_item),
                ).grid(
                    row=grid_row,
                    column=grid_col,
                    sticky="ew",
                    pady=(0, 8),
                    padx=(0, 10 if grid_col < columns - 1 else 0),
                )

    def _command_section_name(self, command):
        explicit = str(command.get("section", "")).strip()
        if explicit:
            return explicit

        label = str(command.get("label", "")).lower()
        raw_command = str(command.get("command", "")).lower()
        if "sweetshelves" in label or "sweetshelves" in raw_command or "/opt/sweetshelves" in raw_command:
            return "Sweet Shelves"
        return "Server"

    def _command_style_name(self, command):
        if command.get("tone") == "danger":
            return "Danger.TButton"
        if command.get("tone") == "accent":
            return "Accent.TButton"
        if command.get("tone") == "success":
            return "Success.TButton"
        if command.get("tone") == "warning":
            return "Warning.TButton"
        if command.get("tone") == "info":
            return "Info.TButton"

        label = str(command.get("label", "")).lower()
        if "backup" in label:
            return "Success.TButton"
        if "usb" in label or "disk" in label or "restore" in label:
            return "Warning.TButton"
        if "pull" in label or "update" in label:
            return "Primary.TButton"
        return "Secondary.TButton"

    def _sync_command_scroll_region(self, _event=None):
        if hasattr(self, "command_canvas"):
            self.command_canvas.configure(scrollregion=self.command_canvas.bbox("all"))

    def _resize_command_canvas_window(self, event):
        if hasattr(self, "command_canvas_window"):
            self.command_canvas.itemconfigure(self.command_canvas_window, width=event.width)

    def test_connection(self):
        self.save_settings()
        self.connection_state_var.set("Testing")
        self.connection_detail_var.set("Trying to reach the server over SSH...")
        self._update_connection_mini_status()
        self._append_activity("Testing SSH connection.")
        self._spawn_worker("test_connection", self._test_connection_worker)

    def refresh_all(self):
        self.refresh_stats()
        self.refresh_logs()

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

    def execute_command(self, command_item):
        label = command_item.get("label", "Command")
        if command_item.get("action") == "open_terminal":
            self.open_server_terminal()
            return
        if command_item.get("confirm"):
            approved = messagebox.askyesno("Confirm command", f"Run '{label}' on the remote server?")
            if not approved:
                return
        self.command_counter += 1
        job_id = f"command_{self.command_counter}"
        self._append_activity(f"Running '{label}'...")
        self._spawn_worker(job_id, self._command_worker, command_item)

    def open_server_terminal(self):
        connection = self.config_data.get("connection", {})
        host = str(connection.get("host", "")).strip()
        user = str(connection.get("user", "")).strip()
        port = str(connection.get("port", 22)).strip()
        key_path = str(connection.get("key_path", "")).strip()

        if not host or not user:
            messagebox.showerror("Missing connection details", "Host and user are required before opening a server terminal.")
            return

        ssh_parts = ["ssh"]
        if key_path:
            ssh_parts.extend(["-i", key_path])
        if port and port != "22":
            ssh_parts.extend(["-p", port])
        ssh_parts.append(f"{user}@{host}")
        ssh_command = " ".join(f'"{part}"' if " " in part else part for part in ssh_parts)

        try:
            if sys.platform == "win32":
                windows_terminal = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "wt.exe"
                if windows_terminal.exists():
                    subprocess.Popen(
                        [str(windows_terminal), "new-tab", "powershell", "-NoExit", "-Command", ssh_command],
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                else:
                    subprocess.Popen(
                        ["powershell.exe", "-NoExit", "-Command", ssh_command],
                        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                    )
            else:
                subprocess.Popen(["x-terminal-emulator", "-e", ssh_command])
            self._append_activity(f"Opened terminal for {user}@{host}.")
        except Exception as exc:
            messagebox.showerror("Terminal launch failed", str(exc))
            self._append_activity(f"Opening terminal failed: {exc}")

    def _spawn_worker(self, job_type, target, payload=None):
        worker = threading.Thread(target=target, args=(job_type, payload), daemon=True)
        worker.start()

    def _test_connection_worker(self, job_type, _payload):
        try:
            result = self.ssh.run("printf connected", timeout=12)
            if result.returncode == 0 and "connected" in (result.stdout or ""):
                self.event_queue.put({"type": job_type, "ok": True})
            else:
                error_output = (result.stderr or result.stdout or "").strip()
                self.event_queue.put({"type": job_type, "ok": False, "error": error_output})
        except Exception as exc:
            self.event_queue.put({"type": job_type, "ok": False, "error": str(exc)})

    def _stats_worker(self, job_type, _payload):
        try:
            result = self.ssh.run(REMOTE_STATS_SCRIPT, timeout=15)
            if result.returncode != 0:
                self.event_queue.put({"type": job_type, "ok": False, "error": (result.stderr or result.stdout).strip()})
                return
            stats = json.loads(result.stdout.strip())
            self.event_queue.put({"type": job_type, "ok": True, "stats": stats})
        except Exception as exc:
            self.event_queue.put({"type": job_type, "ok": False, "error": str(exc)})

    def _logs_worker(self, job_type, _payload):
        log_name = self.log_choice_var.get().strip()
        chosen = None
        for log_item in self.config_data.get("logs", []):
            if log_item.get("name") == log_name:
                chosen = log_item
                break
        if chosen is None and self.config_data.get("logs"):
            chosen = self.config_data["logs"][0]

        if chosen is None:
            self.event_queue.put({"type": job_type, "ok": True, "log_name": "No logs", "content": "No logs configured."})
            return

        try:
            result = self.ssh.run(chosen.get("command", "echo No log command configured"), timeout=15)
            if result.returncode != 0:
                self.event_queue.put(
                    {
                        "type": job_type,
                        "ok": False,
                        "log_name": chosen.get("name", "Log"),
                        "error": (result.stderr or result.stdout).strip(),
                    }
                )
                return
            self.event_queue.put(
                {
                    "type": job_type,
                    "ok": True,
                    "log_name": chosen.get("name", "Log"),
                    "content": result.stdout.strip() or "(No output)",
                }
            )
        except Exception as exc:
            self.event_queue.put({"type": job_type, "ok": False, "log_name": chosen.get("name", "Log"), "error": str(exc)})

    def _command_worker(self, job_type, command_item):
        label = command_item.get("label", "Command")
        command = command_item.get("command", "")
        try:
            result = self.ssh.run(command, timeout=90)
            output = (result.stdout or "").strip()
            error = (result.stderr or "").strip()
            self.event_queue.put(
                {
                    "type": job_type,
                    "ok": result.returncode == 0,
                    "label": label,
                    "output": output,
                    "error": error,
                    "code": result.returncode,
                }
            )
        except Exception as exc:
            self.event_queue.put({"type": job_type, "ok": False, "label": label, "error": str(exc), "code": -1})

    def _drain_queue(self):
        while True:
            try:
                message = self.event_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_event(message)
        self.after(200, self._drain_queue)

    def _handle_event(self, message):
        event_type = message.get("type", "")
        if event_type == "test_connection":
            self._handle_connection_test(message)
            return
        if event_type == "stats_refresh":
            self._handle_stats_update(message)
            return
        if event_type == "log_refresh":
            self._handle_logs_update(message)
            return
        if event_type.startswith("command_"):
            self._handle_command_result(message)

    def _handle_connection_test(self, message):
        if message.get("ok"):
            connection = self.config_data["connection"]
            self.connection_state_var.set("Connected")
            self.connection_detail_var.set(f"SSH access confirmed for {connection['user']}@{connection['host']}.")
            self.set_connection_panel_collapsed(True)
            self._update_connection_mini_status()
            self._append_activity("SSH connection verified.")
            self.refresh_all()
        else:
            self.connection_state_var.set("Connection failed")
            self.connection_detail_var.set(message.get("error", "Unknown SSH error"))
            self.set_connection_panel_collapsed(False)
            self._update_connection_mini_status()
            self._append_activity(f"SSH test failed: {message.get('error', 'Unknown error')}")

    def _handle_stats_update(self, message):
        if not message.get("ok"):
            self.connection_state_var.set("Stats unavailable")
            self.connection_detail_var.set(message.get("error", "Unable to read remote stats."))
            self._update_connection_mini_status()
            self._append_activity(f"Stats refresh failed: {message.get('error', 'Unknown error')}")
            return

        stats = message["stats"]
        self.last_stats = stats
        self.connection_state_var.set("Live")
        self.set_connection_panel_collapsed(True)
        self._update_connection_mini_status()
        hostname = stats.get("hostname", "Remote server")
        uptime = stats.get("uptime", "Unknown uptime")
        load_average = stats.get("load_average") or []
        load_text = " / ".join(str(item) for item in load_average[:3]) if load_average else "No load average"
        self.server_identity_var.set(f"{hostname}  |  Uptime {uptime}")
        self.summary_var.set(f"Load average {load_text}. Metrics are refreshing every {REFRESH_INTERVAL_MS // 1000} seconds.")

        cpu_percent = stats.get("cpu_percent")
        self.cpu_card.set_value(cpu_percent, self._percent_text(cpu_percent), f"Load {load_text}")

        memory = stats.get("memory", {})
        memory_percent = memory.get("percent")
        memory_subtitle = f"{memory.get('used_mb', '--')} MB used / {memory.get('total_mb', '--')} MB total"
        self.memory_card.set_value(memory_percent, self._percent_text(memory_percent), memory_subtitle)

        disk = stats.get("disk", {})
        disk_percent = disk.get("percent")
        disk_subtitle = f"{disk.get('used_gb', '--')} GB used / {disk.get('total_gb', '--')} GB total"
        self.disk_card.set_value(disk_percent, self._percent_text(disk_percent), disk_subtitle)

        usb = stats.get("usb", {})
        usb_percent = usb.get("percent")
        usb_value = f"{usb.get('free_gb', '--')} GB free" if usb.get("free_gb") is not None else "No USB"
        usb_subtitle = usb.get("path") or "No mounted backup USB found"
        self.usb_card.set_value(usb_percent, usb_value, usb_subtitle)

        temperature = stats.get("temperature_c")
        if isinstance(temperature, (int, float)):
            temp_percent = min(max((float(temperature) / 90.0) * 100.0, 0.0), 100.0)
            temp_value_text = f"{temperature:.1f} C"
        else:
            temp_percent = None
            temp_value_text = "Unavailable"
        temp_subtitle = self._temperature_status(temperature)
        self.temp_card.set_value(temp_percent, temp_value_text, temp_subtitle)

        service = stats.get("service", {})
        self.service_card.set_status(
            service.get("active", False),
            service.get("uptime"),
        )

    def _handle_logs_update(self, message):
        if not message.get("ok"):
            content = f"[{message.get('log_name', 'Log')}]\n\n{message.get('error', 'Unable to read logs.')}"
            self._set_text(self.log_text, content)
            self._append_activity(f"Log refresh failed for {message.get('log_name', 'log')}.")
            return

        title = message.get("log_name", "Log")
        content = f"[{title}]\n\n{message.get('content', '')}"
        self._set_text(self.log_text, content)

    def _handle_command_result(self, message):
        label = message.get("label", "Command")
        if message.get("ok"):
            output = message.get("output", "")
            snippet = output[:900] + ("\n..." if len(output) > 900 else "")
            rendered = f"{label} completed."
            if snippet:
                rendered += f"\n\n{snippet}"
            self._append_activity(rendered)
        else:
            error_text = message.get("error") or message.get("output") or "Command failed."
            self._append_activity(f"{label} failed (exit {message.get('code', '?')}):\n{error_text}")

        if "status" in label.lower() or "restart" in label.lower() or "reboot" in label.lower():
            self.after(1200, self.refresh_all)

    def _append_activity(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.activity_text.configure(state="normal")
        self.activity_text.insert("end", f"[{timestamp}] {message}\n\n")
        self.activity_text.see("end")
        self.activity_text.configure(state="disabled")

    def _set_text(self, widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _percent_text(self, value):
        if isinstance(value, (int, float)):
            return f"{value:.1f}%"
        return "Unavailable"

    def _temperature_status(self, temperature):
        if not isinstance(temperature, (int, float)):
            return "No sensor data returned"
        if temperature >= 80:
            return "Hot: investigate cooling soon"
        if temperature >= 65:
            return "Warm: elevated but acceptable"
        return "Healthy temperature range"


def main():
    enable_windows_dpi_awareness()
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--self-test", action="store_true", help="Validate config creation and exit.")
    args = parser.parse_args()

    if args.self_test:
        config = load_or_create_config()
        print(json.dumps({"config_path": str(config_path()), "host": config["connection"]["host"]}))
        return

    app = RemoteServerConsole()
    app.mainloop()


if __name__ == "__main__":
    main()
