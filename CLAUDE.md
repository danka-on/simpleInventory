# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Sweet Shelves — a Flask-based inventory management system for e-commerce resellers. Tracks warehouse positions via barcode scanning, integrates Amazon SP-API and eBay APIs, processes Bill of Lading (BOL) Excel files, and prints thermal labels via Bluetooth ESC/POS printers. Production target is Raspberry Pi.

## Running

```powershell
# Windows development
.\start-app.ps1              # starts Flask + Cloudflare tunnel
.\start-app.ps1 -NoTunnel   # local only

# Raspberry Pi production
sudo systemctl start sweetshelves.service
sudo journalctl -u sweetshelves.service -f
# Or manually: gunicorn -c gunicorn_config.py app:app
```

## Deploying to Pi

Pi host: `dk@10.0.0.151`, SSH key: `C:/Users/boxatron/.ssh/sweet_shelves_pi`, app directory: `/opt/sweetshelves`

```powershell
# Scripted deploy (hardcoded subset of files — update script when adding new files)
.\deploy-to-pi.ps1

# Manual SCP for specific files
scp -i C:/Users/boxatron/.ssh/sweet_shelves_pi .\app.py dk@10.0.0.151:/opt/sweetshelves/
scp -i C:/Users/boxatron/.ssh/sweet_shelves_pi .\templates\*.html dk@10.0.0.151:/opt/sweetshelves/templates/
scp -i C:/Users/boxatron/.ssh/sweet_shelves_pi .\static\i18n.js dk@10.0.0.151:/opt/sweetshelves/static/
```

**Never SCP database files (.db) from PC to Pi.** The Pi has its own live databases. Only transfer code, templates, and static assets.

After deploying, restart the service on the Pi: `sudo systemctl restart sweetshelves.service`

## Lister extension (several agent sessions work on it at once)

Several Claude sessions share this worktree and edit `lister-extension/` at the same time. Rules:

- **Never change `"version"` in `lister-extension/manifest.json` by hand.** Publishing sets it: the live feed's version + 1.
- **Commit only your own files and hunks.** Never `git add -A` or commit a whole shared file that holds someone else's uncommitted edits. Use `git commit -o <your files>`, or build HEAD + your hunk.
- **Publish only with `tools/publish-lister.ps1`.** It takes a lock on the Pi, refuses if HEAD is behind GitHub or lacks the commit the live feed was built from, and runs the tests on a clean export of HEAD, so uncommitted work never ships. It pushes a one-line release commit before uploading, and the Pi refuses a version that isn't newer. Commit and push your change first; anything uncommitted is left out and listed as a warning.
- If publish says "Pull or rebase first" or "publish again", do that. Never bypass it by copying files to the Pi's `static/lister/` yourself.

## Architecture

- **`app.py`** (~14k lines) — monolithic Flask app with 227+ routes. All routing, API endpoints, and view logic lives here.
- **`DBmanager.py`** — SQLite connection pooling (request-scoped via Flask `g`), CRUD operations, WAL mode on all databases.
- **`amazon_manager.py`** / **`ebay_manager.py`** — marketplace API integrations with rate limiting and OAuth token management.
- **`BOLextractor.py`** — parses BOL data from Excel/CSV uploads.
- **`printer_manager.py`** — Bluetooth/serial/network thermal printer support (ESC/POS protocol), cross-platform with Pi detection.
- **`rotating_backup.py`** — automated SQLite backup rotation.

### Database Layout (12 SQLite files)

Key databases: `searchRack.db` (main inventory + positions), `bol.db` (BOL items), `rawbol.db` (raw BOL uploads + costs), `sold.db` (sales across marketplaces), `amazonStore.db` / `ebayStore.db` (marketplace listings/orders), `rackhistory.db` (audit trail), `deleted.db` (soft-delete/undo).

### Data Flow

Raw BOL upload → `rawbol.db` → scan to position → `searchRack.db` → enrich from marketplace APIs → sales tracked in `sold.db` → financial analytics aggregated from sold + fee APIs.

### Frontend

Server-side Jinja2 templates in `templates/`. Vanilla JavaScript with AJAX calls. CSS/JS mostly inline in templates. `static/i18n.js` for internationalization.

## Key Conventions

- Database connections use `get_db_connection(db_name)` or the `@contextmanager db_connection(db_name)` pattern from DBmanager — never open raw `sqlite3.connect()` calls in app.py.
- Flask-Caching with 5-minute default timeout. Flask-Compress enabled.
- Error responses use `_safe_error()` to sanitize messages before sending to client.
- Pi deployment: 1 Gunicorn worker, 4 threads, preloaded app, RAM disk for temp files.
- No automated test suite exists — testing is manual through the web UI.
