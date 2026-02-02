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

Pi host: `manager@superinventory.local`, app directory: `/opt/sweetshelves`

```powershell
# Scripted deploy (hardcoded subset of files — update script when adding new files)
.\deploy-to-pi.ps1

# Manual SCP for specific files
scp .\app.py manager@superinventory.local:/opt/sweetshelves/
scp .\templates\*.html manager@superinventory.local:/opt/sweetshelves/templates/
scp .\static\i18n.js manager@superinventory.local:/opt/sweetshelves/static/
```

**Never SCP database files (.db) from PC to Pi.** The Pi has its own live databases. Only transfer code, templates, and static assets.

After deploying, restart the service on the Pi: `sudo systemctl restart sweetshelves.service`

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
