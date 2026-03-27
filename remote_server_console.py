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
        self.command_counter = 0
        self.last_stats = {}
        self.connection_collapsed = False

        self.host_var    = tk.StringVar(value=str(self.config_data["connection"].get("host", "")))
        self.user_var    = tk.StringVar(value=str(self.config_data["connection"].get("user", "")))
        self.port_var    = tk.StringVar(value=str(self.config_data["connection"].get("port", 22)))
        self.key_path_var = tk.StringVar(value=str(self.config_data["connection"].get("key_path", "")))
        self.timeout_var = tk.StringVar(value=str(self.config_data["connection"].get("connect_timeout_seconds", 8)))
        self.log_choice_var      = tk.StringVar()
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

        # Middle: connection drawer (collapsible left) + commands (right)
        middle = tk.Frame(self, bg=BASE)
        middle.pack(fill="x", padx=0)
        self._conn_drawer_outer = middle

        self._build_commands_section(middle)
        _sep(self, SURFACE2, height=1)
        self._build_logs_section()

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
        self._mk_btn(right, "Settings",
                     lambda: self._toggle_conn_drawer(),
                     bg=SURFACE1, fg=SUBTEXT1, font=("Segoe UI", 9)
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

    # ── Commands section ──────────────────────────────────────────────────────
    def _build_commands_section(self, parent):
        frame = tk.Frame(parent, bg=BASE)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=2)

        # Connection drawer slot (col 0 when visible, otherwise hidden)
        self._conn_col = tk.Frame(frame, bg=MANTLE, width=0)
        self._conn_col.grid(row=0, column=0, sticky="nsew")
        self._conn_col.grid_remove()
        self._build_conn_drawer_content(self._conn_col)
        self._conn_frame_ref = frame

        # Sweet Shelves commands (col 0 normally, col 1 when drawer open)
        ss_panel = self._build_command_group(frame, "Sweet Shelves", "Sweet Shelves")
        ss_panel.grid(row=0, column=1, sticky="nsew", padx=0)
        self._ss_cmd_panel = ss_panel

        # Divider
        tk.Frame(frame, bg=SURFACE2, width=1).grid(row=0, column=1, sticky="nse")

        # Server commands (col 2)
        srv_panel = self._build_command_group(frame, "Server", "Server")
        srv_panel.grid(row=0, column=2, sticky="nsew")
        self._srv_cmd_panel = srv_panel

        self._cmd_frame = frame

        # Store button parent refs for re-render
        self.command_buttons_parent = frame

    def _build_command_group(self, parent, title, section_key):
        panel = tk.Frame(parent, bg=BASE)

        # Section header
        header = tk.Frame(panel, bg=MANTLE)
        header.pack(fill="x")
        tk.Label(header, text=title.upper(),
                 font=("Segoe UI Semibold", 9),
                 fg=SUBTEXT0, bg=MANTLE,
                 padx=18, pady=10).pack(side="left")

        # Buttons container
        btn_area = tk.Frame(panel, bg=BASE)
        btn_area.pack(fill="both", expand=True, padx=14, pady=12)

        commands = [c for c in self.config_data.get("commands", [])
                    if self._command_section_name(c) == section_key]

        for cmd in commands:
            bg, fg = self._cmd_colors(cmd)
            row = tk.Frame(btn_area, bg=BASE)
            row.pack(fill="x", pady=3)
            self._mk_btn(
                row, cmd.get("label", "?"),
                lambda c=cmd: self.execute_command(c),
                bg=bg, fg=fg,
                font=("Segoe UI Semibold", 10),
                padx=16, pady=8,
                anchor="w",
            ).pack(fill="x")

        return panel

    def _cmd_colors(self, cmd):
        tone = cmd.get("tone", "secondary")
        mapping = {
            "accent":    (BLUE,    CRUST),
            "primary":   (SAPPHIRE, CRUST),
            "success":   (GREEN,   CRUST),
            "warning":   (PEACH,   CRUST),
            "danger":    (RED,     CRUST),
            "info":      (MAUVE,   CRUST),
            "secondary": (SURFACE1, SUBTEXT1),
        }
        # Auto-infer from label if tone not set
        if tone not in mapping:
            label = cmd.get("label", "").lower()
            if "restart" in label or "reboot" in label:
                return mapping["accent"]
            if "backup" in label and "restore" not in label:
                return mapping["success"]
            if "restore" in label:
                return mapping["warning"]
            if "terminal" in label:
                return mapping["info"]
            return mapping["secondary"]
        return mapping[tone]

    # ── Connection drawer ─────────────────────────────────────────────────────
    def _build_conn_drawer_content(self, parent):
        inner = tk.Frame(parent, bg=MANTLE)
        inner.pack(fill="both", expand=True, padx=0)

        # Header
        hdr = tk.Frame(inner, bg=MANTLE)
        hdr.pack(fill="x", padx=18, pady=(14, 0))
        tk.Label(hdr, text="CONNECTION", font=("Segoe UI Semibold", 9),
                 fg=SUBTEXT0, bg=MANTLE).pack(side="left")
        self._mk_btn(hdr, "✕", self._toggle_conn_drawer,
                     bg=SURFACE0, fg=SUBTEXT0,
                     font=("Segoe UI", 9), padx=8, pady=3
                     ).pack(side="right")

        _sep(inner, SURFACE2, pady=(10, 0))

        body = tk.Frame(inner, bg=MANTLE)
        body.pack(fill="both", expand=True, padx=18, pady=14)

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
                     ).pack(anchor="w", pady=(8, 2))
            row = tk.Frame(body, bg=MANTLE)
            row.pack(fill="x")
            entry = ttk.Entry(row, textvariable=var, width=24)
            entry.pack(side="left", fill="x", expand=True)
            if label == "SSH Key":
                self._mk_btn(row, "Browse", self.browse_key,
                             bg=SURFACE1, fg=SUBTEXT1,
                             font=("Segoe UI", 9), padx=8, pady=4
                             ).pack(side="left", padx=(6, 0))

        _sep(body, SURFACE2, pady=(14, 0))

        btn_row = tk.Frame(body, bg=MANTLE)
        btn_row.pack(fill="x", pady=(10, 0))
        self._mk_btn(btn_row, "Save & Connect", self.test_connection,
                     bg=BLUE, fg=CRUST,
                     font=("Segoe UI Semibold", 10), padx=14, pady=8
                     ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._mk_btn(btn_row, "Save Only", self.save_settings,
                     bg=SURFACE1, fg=SUBTEXT1,
                     font=("Segoe UI", 10), padx=10, pady=8
                     ).pack(side="left")

        _sep(body, SURFACE2, pady=(14, 0))

        # Status indicator
        self._conn_status_lbl = tk.Label(
            body, textvariable=self.conn_state_var,
            font=("Segoe UI Semibold", 11), fg=SUBTEXT0, bg=MANTLE)
        self._conn_status_lbl.pack(anchor="w", pady=(10, 2))
        tk.Label(body, textvariable=self.conn_detail_var,
                 font=("Segoe UI", 9), fg=SUBTEXT0, bg=MANTLE,
                 wraplength=220, justify="left").pack(anchor="w")

    def _toggle_conn_drawer(self):
        col = self._conn_col
        if self._conn_drawer_visible:
            col.grid_remove()
            self._conn_drawer_visible = False
        else:
            col.configure(width=280)
            col.grid()
            self._conn_drawer_visible = True

    # ── Logs section ──────────────────────────────────────────────────────────
    def _build_logs_section(self):
        outer = tk.Frame(self, bg=MANTLE)
        outer.pack(fill="both", expand=True)

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True, padx=0, pady=0)

        # Server logs tab
        log_tab = tk.Frame(notebook, bg=MANTLE)
        notebook.add(log_tab, text="  Server Logs  ")
        log_tab.columnconfigure(0, weight=1)
        log_tab.columnconfigure(1, weight=0)
        log_tab.rowconfigure(1, weight=1)

        log_hdr = tk.Frame(log_tab, bg=MANTLE)
        log_hdr.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(10, 6))
        tk.Label(log_hdr, text="LOG SOURCE",
                 font=("Segoe UI", 8, "bold"),
                 fg=SUBTEXT0, bg=MANTLE).pack(side="left", padx=(0, 10))
        self.log_combo = ttk.Combobox(log_hdr, textvariable=self.log_choice_var,
                                      state="readonly", width=28)
        self.log_combo.pack(side="left")
        self.log_combo.bind("<<ComboboxSelected>>", lambda _: self.refresh_logs())

        self.log_text = tk.Text(
            log_tab, height=10,
            bg=CRUST, fg=SUBTEXT1,
            insertbackground=TEXT,
            relief="flat", wrap="none",
            padx=14, pady=12,
            font=("Consolas", 9),
        )
        self.log_text.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.log_text.configure(state="disabled")

        # Activity log tab
        act_tab = tk.Frame(notebook, bg=MANTLE)
        notebook.add(act_tab, text="  Activity  ")
        act_tab.rowconfigure(0, weight=1)
        act_tab.columnconfigure(0, weight=1)

        self.activity_text = tk.Text(
            act_tab, height=10,
            bg=CRUST, fg=SUBTEXT1,
            insertbackground=TEXT,
            relief="flat", wrap="word",
            padx=14, pady=12,
            font=("Consolas", 9),
        )
        self.activity_text.grid(row=0, column=0, sticky="nsew")
        self.activity_text.configure(state="disabled")

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _mk_btn(parent, text, command, *,
                bg=SURFACE1, fg=TEXT,
                font=("Segoe UI Semibold", 10),
                padx=14, pady=7, anchor="center", **kw):
        """Create a styled tk.Button (not ttk — full color control)."""
        btn = tk.Button(
            parent, text=text, command=command,
            bg=bg, fg=fg, activebackground=_brighten(bg), activeforeground=fg,
            font=font, relief="flat", bd=0,
            padx=padx, pady=pady, cursor="hand2",
            anchor=anchor, **kw,
        )
        return btn

    def render_command_buttons(self):
        """Rebuild command buttons when config changes."""
        for panel in [self._ss_cmd_panel, self._srv_cmd_panel]:
            panel.destroy()
        self._ss_cmd_panel = self._build_command_group(
            self._cmd_frame, "Sweet Shelves", "Sweet Shelves")
        self._ss_cmd_panel.grid(row=0, column=1, sticky="nsew")
        self._srv_cmd_panel = self._build_command_group(
            self._cmd_frame, "Server", "Server")
        self._srv_cmd_panel.grid(row=0, column=2, sticky="nsew")

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
        if "sweetshelves" in label or "/opt/sweetshelves" in raw:
            return "Sweet Shelves"
        return "Server"

    # ── Workers & queue ───────────────────────────────────────────────────────
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
        if svc.get("active"):
            up = svc.get("uptime") or "running"
            self._ss_tile.set_live("LIVE", GREEN, f"Up {up}", GREEN)
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
