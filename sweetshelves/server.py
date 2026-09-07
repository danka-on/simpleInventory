"""Server for Sweet Shelves."""

import os
import requests
import subprocess
import time
from . import runtime as ss_runtime


def start_flask():
    ss_runtime.app.run(host="0.0.0.0", port=8080)


def start_tunnel():
    if os.getenv('SWEETSHELVES_NO_TUNNEL', '').strip().lower() in ('1', 'true', 'yes'):
        return
    config_path = os.getenv('CLOUDFLARED_CONFIG', os.path.normpath(os.path.expanduser('~/.cloudflared/config.yml')))
    subprocess.Popen([
        "cloudflared",
        "tunnel",
        "--config",
        config_path,
        "run",
        "mytunnel"
    ])
    print("⏳ Cloudflare tunnel starting...")
    time.sleep(3)
    try:
        res = requests.get("http://localhost:5555/metrics", timeout=5)
        for line in res.text.splitlines():
            if "userURL" in line:
                public_url = line.split(" ")[-1]
                print(f"✅ Public URL: {public_url}")
                break
    except Exception as e:
        print("❌ Tunnel metrics not found:", e)
