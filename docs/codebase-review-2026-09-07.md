# Codebase review — September 7, 2026

The review covered the Python application and support modules, route registration,
Jinja templates, browser scripts, startup configuration, backups, and deployment
scripts. Existing and concurrently edited listing/scanner work was preserved.
Application imports and regression tests used a disposable copy with temporary
databases and background services disabled. No production database or service was
changed, and nothing was deployed.

## Fixes

- **Startup and error handling:** Database index setup tolerates an empty checkout
  or failed connection. Connection cleanup in dozens of routes no longer hides
  the original failure with an uninitialized-variable exception. Removed a
  duplicate disabled sold-removal route and its unreachable stock-changing code.
- **BOL inventory:** Sync, desync, and lot deletion now update BOL quantities and
  sync markers through one SQLite connection with an attached database. An
  immediate transaction prevents concurrent syncs from adding a lot twice, and
  failures roll back both sets of changes. Unsynced lots are never subtracted.
  Zero quantities remain zero, and older schemas work without optional prep
  quantity columns. A composite UPC/lot/date index avoids repeated full-table
  scans; lot counts are calculated from the already-loaded rows.
- **Marketplace sales:** New sales store a stable order reference. Deleting a sale
  removes its matching order instead of treating its unit quantity as the number
  of same-day orders to delete. Ambiguous legacy matches are rejected before
  changing either database. Related return changes and sale deletion roll back
  together on failure. Failed order creation removes the newly created sale and
  stops before decrementing warehouse stock. Marketplace connections close on
  early returns and errors, and use the application directory rather than the
  shell's current directory.
- **eBay fees:** The fee sync allocates each order-level fee across its lines by
  line revenue, matching the importer's approach. Cent rounding preserves the
  exact order total, including when all line prices are zero. Other stores are
  not changed.
- **Barcodes and refreshes:** Normalizing a long numeric barcode no longer rounds
  it through a float. Data-version updates use an atomic, increasing value so
  rapid changes cannot disappear within the same clock tick. Version connections
  close automatically.
- **Backups:** SQLite handles close before files are compressed or removed. WAL
  snapshots become self-contained. Completed gzip files are published by atomic
  replacement; failed compression leaves no apparent completed backup. Backup
  names include microseconds, invalid retention counts are rejected, and database
  names containing hyphens can be filtered correctly.
- **Tokens and HTTP:** Token refresh is serialized across threads, starts shortly
  before expiry, and replaces its JSON file atomically. OAuth requests, Amazon
  feed uploads, report downloads, and tunnel-metrics requests have timeouts.
  Report-download HTTP failures are checked before parsing.
- **Runtime:** Gunicorn uses one threaded worker for process-local job state and
  caches, and loads the application after forking so background threads run in
  the worker. The cache scheduler also starts in production. Unversioned static
  assets revalidate after deployments. An unset Flask secret uses a random value
  instead of a publicly known fallback; configure `FLASK_SECRET_KEY` to keep
  sessions stable across restarts. The launcher honors `-NoTunnel`, restores the
  caller's environment and directory, and legacy inventory-cache paths are
  anchored to the application directory.
- **Support files:** The complete requirements list includes `psutil`; the
  standalone returns helper imports the symbols it uses; placeholder SP-API
  reference code is documentation rather than an invalid executable module.
  The deployment script includes the imported local modules and current browser
  assets, works from another directory, and stops on a failed transfer.

## Validation

All 133 isolated Python tests and all nine JavaScript/browser suites pass,
including 27 new Python regression tests from this review. The browser checks
cover listing edits, matching/undo, stale responses, barcode
receiving, feedback failures, and the phone/tablet keyboard. Additional checks
parse all Python source, Jinja templates, browser scripts, and PowerShell scripts;
check for duplicate route/method registrations; and check diff whitespace.
Syntax checks passed for 68 Python files, 71 Jinja templates, 83 JavaScript
files/inline blocks, and seven PowerShell scripts. The final combined run is
recorded in `codebase-review-test-results.txt` beside this document.

Run Python tests from this project directory:

```powershell
py -3.13 tools/run_checks.py
```

Include the JavaScript and headless Edge suites (requires Playwright and Edge):

```powershell
$env:NODE_PATH = 'C:\Users\boxatron\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
py -3.13 tools/run_checks.py --javascript
```

Both commands copy code into a temporary directory first. The manual API and
mail-sending diagnostic scripts are not executed by test discovery.

These checks do not exercise live Amazon/eBay accounts, physical printers,
Cloudflare, Raspberry Pi service management, or every route against production
data. Attached-database rollback is tested for application/SQL failures; SQLite
WAL mode does not guarantee an all-or-nothing multi-file commit after power loss.
The available local Python 3.13 environment was used, rather than rebuilding the
Pi's pinned dependency environment.

## Optional deployment handoff

Run from the project directory only when ready to deploy the reviewed runtime
files. These commands exclude all databases, credentials, and Windows-only files.
Concurrent listing changes may need their own additional files.

```powershell
scp -i C:/Users/boxatron/.ssh/sweet_shelves_pi .\app.py .\amazon_manager.py .\amazon_returns_method.py .\ebay_manager.py .\ebay_oauth_setup.py .\get_ebay_tokens.py .\gunicorn_config.py .\inventory.py .\marketplace_manager.py .\rawbol_manager.py .\rotating_backup.py .\token_manager.py .\requirements_full.txt dk@10.0.0.151:/opt/sweetshelves/
ssh -i C:/Users/boxatron/.ssh/sweet_shelves_pi dk@10.0.0.151 "sudo systemctl restart sweetshelves.service"
```
