"""Sweet Shelves WSGI and local-development entrypoint.

Feature implementations live in the sweetshelves package.
"""

import threading
import time

from sweetshelves.bootstrap import app
from sweetshelves.legacy import resolve as __getattr__
from sweetshelves import server as ss_server


def main():
    """Run the local server and optional Cloudflare tunnel."""
    flask_thread = threading.Thread(target=ss_server.start_flask, daemon=True)
    flask_thread.start()
    time.sleep(1)
    try:
        ss_server.start_tunnel()
    except Exception as e:
        print(f"Warning: Tunnel start failed: {e}")

    # Keep main thread alive
    while True:
        time.sleep(1)


if __name__ == '__main__':
    main()
