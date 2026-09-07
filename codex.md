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

Pi host: `dk@10.0.0.151`, app directory: `/opt/sweetshelves`
Pi SSH key: `C:/Users/boxatron/.ssh/sweet_shelves_pi`

Always use the Pi key explicitly with `ssh -i` / `scp -i` when connecting to `dk@10.0.0.151`.
The default SSH config on this PC only has a GitHub host entry, so Pi connections can fail with `Permission denied (publickey,password)` unless the key is passed explicitly.

```powershell
# Scripted deploy (runtime module list plus the sweetshelves package)
.\deploy-to-pi.ps1

# Manual SCP for specific files
scp -i C:/Users/boxatron/.ssh/sweet_shelves_pi .\app.py dk@10.0.0.151:/opt/sweetshelves/
scp -i C:/Users/boxatron/.ssh/sweet_shelves_pi .\templates\*.html dk@10.0.0.151:/opt/sweetshelves/templates/
scp -i C:/Users/boxatron/.ssh/sweet_shelves_pi .\static\i18n.js dk@10.0.0.151:/opt/sweetshelves/static/
```

**Never SCP database files (.db) from PC to Pi.** The Pi has its own live databases. Only transfer code, templates, and static assets. Since the modularization, deploy `sweetshelves/` along with `app.py`; copying the entrypoint alone is insufficient. The deploy script stages only package Python source and transfers it before the entrypoint.

After deploying, restart the service on the Pi: `ssh -i C:/Users/boxatron/.ssh/sweet_shelves_pi dk@10.0.0.151 "sudo systemctl restart sweetshelves.service"`

### Preferred one-liner deploy (known working)

Use this when asked to "push", "p", or "push to pi" with a specific file list and no DB transfer:

```powershell
$h='dk@10.0.0.151'; $k='C:/Users/boxatron/.ssh/sweet_shelves_pi'; $t="$env:TEMP\pi-deploy.tar"; tar -cf $t DBmanager.py app.py static/shelf-creator.js templates/bulk_manifest.html templates/item_prep.html templates/item_prep_diagnostic.html templates/item_prep_diagnostic_view.html templates/items_to_list.html templates/movelocation.html templates/pictureposition.html templates/ready_to_ship.html templates/shelfmanager.html templates/tools.html templates/unified_search.html; scp -i $k $t "${h}:/tmp/pi-deploy.tar"; ssh -i $k $h "tar -xf /tmp/pi-deploy.tar -C /opt/sweetshelves && rm -f /tmp/pi-deploy.tar && sudo systemctl restart sweetshelves"; Remove-Item $t -Force
```

This workflow:
- uploads only the selected files via tar,
- extracts into `/opt/sweetshelves`,
- deletes the remote tar,
- restarts `sweetshelves`,
- deletes the local temp tar.

### User deployment preference (2026-03-05)

When user says "push", "p", "let's push", or "push to pi":
- provide only the direct `scp` commands for the relevant files (no auto-execution),
- put all `scp` commands and the restart command together in one copy/pasteable PowerShell code block,
- always include the explicit Pi key flag: `-i C:/Users/boxatron/.ssh/sweet_shelves_pi`,
- when multiple template files are being pushed, combine them into a single `scp` line to `/opt/sweetshelves/templates/`,
- always use this exact restart command as the final line: `ssh -i C:/Users/boxatron/.ssh/sweet_shelves_pi dk@10.0.0.151 "sudo systemctl restart sweetshelves.service"`,
- exclude all `.db` files,
- wait for user confirmation (`success` or `s`) before updating this memory checkpoint.
- after each code change, include the relevant `scp` push command in the handoff so deployment is always easy to do next.

### Push/commit intent rule

When user says "push", "p", "push to pi", or "commit changes", treat it as:
- deploy/commit only the most recent relevant changes,
- use git diff from the last successful checkpoint forward,
- exclude all `.db` files from transfer.

### Last successful checkpoint

- Status: success confirmed by user
- Date: 2026-03-17 10:04:13 -04:00
- Branch: `pi-claude-refactor`
- Git commit baseline for next incremental file discovery: `51b42e5`
- Note: this deploy included working-tree changes beyond the committed baseline, so use the pushed-file list below to avoid resending already-deployed uncommitted files until the next git checkpoint.
- Files pushed in this successful checkpoint:
  - `app.py`
  - `repair_lotless_special_items.py`
  - `templates/item_prep.html`
  - `templates/item_prep_diagnostic.html`
  - `templates/items_to_list.html`

## Architecture

- **`app.py`** (30 lines) — WSGI/local-development entrypoint. `gunicorn -c gunicorn_config.py app:app` and `python app.py` still work.
- **`sweetshelves/`** — 86 feature/core modules, plus package initialization, startup, route registration, and legacy exports. The migration preserved all 471 Flask URL rules and 1,132 helper/endpoint function signatures. See `docs/architecture.md` for ownership and extension rules.
- **`sweetshelves/bootstrap.py`** — initializes schemas, registers routes, and starts optional workers once. Feature imports do not open databases or start background services.
- **`sweetshelves/runtime.py`**, **`config.py`**, **`database.py`** — shared Flask/cache instances, project-root configuration, request-scoped connections and teardown.
- **`sweetshelves/routing.py`** — explicit registrations preserving existing endpoint names and URLs.
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

- Prefer `sweetshelves.database.get_db_connection(db_name)` / `db_connection(db_name)` for request-scoped work, or `DBmanager.connect_db` for independent connections. Existing feature code still contains legacy raw SQLite connections; keep their transaction behavior intact when changing them.
- Flask-Caching with 5-minute default timeout. Flask-Compress enabled.
- Error responses use `sweetshelves.errors._safe_error()` to sanitize messages before sending to client.
- Pi deployment: 1 Gunicorn worker, 4 threads, `preload_app = False`, RAM disk for temp files.
- Offline regression suite: `py -3.13 tools/run_checks.py --javascript` on Windows, or `python tools/run_checks.py --javascript` in a configured environment. It copies code into a disposable directory, excludes databases/credentials, and disables background services and the tunnel. Browser suites require Playwright and Edge.
- New features import their owning modules directly. `app.__getattr__` preserves legacy read/import access only; patch shared state on its real owner in tests. Do not add features or shared state to `app.py`.
