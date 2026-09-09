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

### Approval failures in retried shipment 4 (2026-09-08)

Live session 5 (`shipment 4`) contains 10 item types; its create-plan operation failed with FBA_INB_0021 for A1-GX7S-49LJ / 8051770493273, B6-ZXW7-ST78 / 8051770493242, and 4U-7LXO-33IJ / 8051770491637. The seven other rows have no errors in that plan response. FNSKU/listing readiness had masked these inbound approval failures.

Added a failed-plan approval parser, authoritative session-payload/scan status overlay for those SKUs, and an approval panel naming every item. Added an explicit `set-aside-approval-items` action/button: atomically removes only approval-blocked rows from a failed unpacked attempt, preserves rejection reasons/history, resets the local plan to draft, and keeps the remainder. It refuses missing confirmation, empty remainder, successful/packed plans, or existing physical scans. No Amazon plan/listing mutation is performed by this action. Live session 5 still contains all 10 items; the user must choose the button or obtain approval.

Deployed fba_shipments.py, fba_inventory.py, and fba_prep.html, restarted and verified active service, live panel, 3 failed/ineligible rows and 7 remaining rows. Synced equivalent Desktop debby functions/template. Validation: 73 focused Python tests, rejection routing in both checkouts, active-group/final-label JS checks, and 84 script syntax checks passed.

### Final shipping-label box mapping deployed (2026-09-08)

The final shipping-label step now lists each original local box number (e.g. BOX-01, BOX-08), group, destination, and Amazon carton ID, with a per-box print link. Sync loads Amazon shipment boxes after transport confirmation and matches by SKU quantities plus dimensions/weight; it does not infer identity from API order or renumber cartons within groups. Ambiguous matches do not offer per-box printing. The existing all-shipment label download remains available. Per-box requests revalidate the mapping against Amazon and request only its matched carton ID. Source contract checked against Amazon SP-API Box schema and official getLabels guidance.

Deployed fba_shipments.py and fba_prep.html, restarted and verified service/page/backend. Desktop debby received equivalent monolith functions and template plus test_fba_final_labels.cjs. 65 focused Python tests, final-label UI test, transport JS, and all 83 script syntax checks passed. Current session remains packing; final shipment-label generation has not yet been exercised against a purchased live shipment. No labels were printed and no shipping purchase was made for verification.

### Per-group active cartons deployed (2026-09-08)

Packing now keeps one active carton per packing group, with independent selectors and card highlights. Scans submit the selected box for the scanned SKU's group. Browser preferences persist per session, migrate the old single selection, and validate removed cartons. The last-active box still supplies worker/move context. Synced `templates/fba_prep.html` and `test_fba_active_groups.cjs` into Desktop `debby`; deployed the template to the Pi, restarted, and verified the live page contains the selectors and group-based scan payload. Tests passed for alternating group scans, independent selection, reload, removal, and session isolation in both checkouts; rejection routing and all 83 inline scripts also passed. No inventory scans or Amazon plan mutations were used for testing.

### Exact-barcode rejection verification (2026-09-08)

Barcode `8051770493273` / SKU `A1-GX7S-49LJ` is intentionally excluded, not an active-plan grouping failure. It and `B6-ZXW7-ST78`, `4U-7LXO-33IJ` had empty archived reasons despite their approval-required inbound rejection. Corrected those three archived records to retain the FBA_INB_0021 approval reason; active items were unchanged. Changed the generic set-aside fallback in scanning and the prep template to describe shipment exclusion without falsely asserting incomplete setup. Deployed both files, verified service active and the exact live label-resolution endpoint returns the approval reason (422, no print/pack). Synced the matching Desktop app/template edits. Validation: 26 focused Python tests and rejection-routing JS checks passed; 83 inline scripts parsed.

### Verified automatic deployment (2026-09-08)

- Deployed `sweetshelves/fba_shipments.py`, `sweetshelves/fba_readiness.py`, and `templates/fba_prep.html` to `/opt/sweetshelves`; restarted service and verified it active and the FBA page serving.
- Refreshed session 4: 127 plan item types, groups of 124 and 3, no ungrouped current items, five recorded packed units preserved. The collapsible unpacked list is live above/below the intended controls (box selection precedes the list).
- Synced the relevant FBA functions into Desktop `debby` monolithic `app.py`, plus FBA prep/label templates. Preserved unrelated top-level code and saved before/after copies in Documents `fba-save-diagnostic/debby-sync`. These are working-tree changes, not a Git commit.
- Validation: 64 focused Python tests passed previously for the deployed pagination revision; all 83 inline scripts parse; rejection voice/routing JavaScript checks pass. Desktop app compiles and unrelated AST nodes are unchanged.
- Remaining investigation: user reports rejected/check-FBA scan messages; all 127 active rows currently have ready setup status. A specific failing barcode has been requested to distinguish a saved excluded item from incorrect matching. Do not bypass real exclusions or approval requirements.

### Automatic deployment preference (2026-09-08)

After each completed, verified code change, automatically deploy the relevant code/templates/static assets to the Pi, restart and verify `sweetshelves.service`, and synchronize the corresponding changes into the Desktop `debby` checkout. The user explicitly authorized this; no additional confirmation is required. This supersedes the older command-only deployment preference. Never transfer databases. Preserve unrelated work and adapt changes to the Desktop monolithic layout as needed. Record verified outcomes accurately.

### Update command preference (2026-09-08)

When the user says "update", execute the relevant deployment commands on the Pi, restart `sweetshelves.service`, and update the Desktop `debby` checkout with those same relevant changes. This is standing authorization to execute, rather than only provide copy/paste commands; do not ask for confirmation again. Use the explicit Pi SSH key, transfer code/templates/static assets only, never transfer database files, and verify service health afterward. Preserve unrelated local changes in `debby`; reconcile the modular Documents checkout with the Desktop layout instead of blindly overwriting it. Record the successful deployment and synchronization only after verification. This preference applies to "update"; the older command-only preference for "push" remains unless the user overrides it.

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

### Main working checkout (2026-09-07)

- Continue development in `C:\Users\boxatron\Documents\ChatGPT\sweetshelves\push-small-parcel-fix`, on branch `codex/main-working-project`. The folder has not moved.
- This is a linked Git worktree of `C:\Users\boxatron\Desktop\simpleInventory`; both folders share the repository history. The Desktop checkout remains on `debby` at `4bff175` (`before overhaul`).
- The modularized Documents checkpoint is preserved on `codex/codebase-overhaul` at `24c08d0`. The main working branch merges both checkpoints, including Desktop FBA/receiving/token changes and the Documents audit/refactor.
- The merged contract has 473 routes and 1,153 function signatures. Validation: 173 Python tests and 10 JavaScript/browser suites passed. See `docs/merged-project-2026-09-07.md` for details and incremental Pi commands.
- The user reported running the earlier Pi commands; this new merged revision has not been deployed by the agent. Keep the last-successful-deployment checkpoint unchanged until the user confirms success for the relevant deployment.

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

### Amazon brand-family resolution deployed (2026-09-08)

Added Resolve on Amazon links to failed FBA queue rows and archived set-aside rows. The dedicated comparison page loads live catalog images, brands, sizes/colors, parents and paged children. Users select a catalog brand, review the exact existing SKU/ASIN, confirm physical packaging, and submit a brand-only patch. Server validates family membership, current issues, selected brand, and listing fingerprint; persistent attempt records prevent duplicate/uncertain retries. No ASIN remapping, shipment quantity changes, or automatic reinstatement. Other issue types link to Seller Central; this release edits brand only.

Deployed amazon_resolution_routes.py, sweetshelves/routing.py, templates/amazon_resolution.html, static/amazon-resolution.js and the FBA template. Service active and route HTTP 200. Live browser verified seven family cards for barcode 194590090586. Eight isolated backend tests pass in both checkouts; browser fixture verified review, required confirmation and accepted-processing result; FBA rejection-routing test and JS syntax checks pass. Desktop monolith received registration plus matching feature files and minimal template edits. No real brand correction submitted during feature verification.

### Persistent FBA Needs attention deployed (2026-09-08)

Implemented fba_attention_routes.py with injected dependencies for both modular main checkout and Desktop monolith, registered at the end of routing.py / before Desktop app's main block. The attention SQLite table lives in searchRack.db. Import uses newest saved session and barcode identity without summing repeated-batch counts; active rows supersede old rejection history. Last check, original/current error, category, quantities and assignment survive reloads. Rechecks read the actual saved FBA offer, preserve approval blockers, and require an inbound eligibility preview after prior approval errors before declaring Ready to retry. Network failures retain the original reason and block transfer.

FBA prep now has Needs attention with barcode/search, category filters, listing-resolution/Listing Agent and Amazon approval links, sequential Recheck all, visible-tab pending activation rechecks, and a destination draft selector. Explicit Add rechecks first and atomically preserves saved quantities, blocks duplicate active stock, locked/packed destinations and concurrent edits, and creates or appends to a counting draft. No inventory decrement or Amazon plan submission occurs in this workflow.

Deployed only fba_attention_routes.py, static/fba-attention.js, templates/fba_prep.html and sweetshelves/routing.py; compile check passed, sweetshelves.service restarted and active. Backups of the two previously existing Pi files are at /tmp/fba-attention-20260908. Desktop debby has the identical feature module/static/template and monolith registration; no databases transferred. 96 focused Python tests passed in isolated copies, including real-schema transfers and route preservation; browser fixture passed search/scanning, escaping, filtering, recheck, transfer destination and mobile layout. Desktop standalone tests: 21 passed, one modular integration test skipped. Four pre-existing architecture signature snapshot assertions still differ because earlier changes added optional parameters (strict/local_boxes/local_box_id); no functions were changed for those assertions. Route fixture was updated additively for the existing brand-resolution routes and the two attention routes.

Live API imported 28 types / 34 units: 25 set-aside types plus 3 active Pratesi plan rejects (7 units). Rechecked all 28 through deployed endpoints: 12 approval, 8 catalog, 5 listing corrections, 1 activation, 2 ready to retry. Ready barcodes 739550332131 and 789323442543 (one each). Shipment item JSON and rejection history byte strings unchanged across rechecks. Live browser confirmed the new tab and Ready to retry filter; did not click Add or submit any shipment. The Pi listens on 127.0.0.1:5000; temporary local SSH forwarding was needed for browser verification.

### FBA setup no longer pre-fails on catalog-quality listing errors (2026-09-09)

Root cause of "Set up N SKUs for FBA" rejecting items that Seller Central converts fine: `_fba_listing_readiness` classified any ERROR-severity Listings Items issue (variation-family brand conflict 100898, missing product_description 90220, invalid color 99022, main image 18320) as `failed` before the FBA patch was ever sent, and `_fba_patch_listing_to_fba` / `_fba_patch_listing_no_battery_safety` / the companion PUT also treated echoed errors as failure even when Amazon returned ACCEPTED. Now only approval/qualification errors (18299, QUALIFICATION_REQUIRED, FBA_INB_0021, restricted) block an offer change; other errors are saved as `fba_listing_notes` (amber "Amazon listing note (does not block FBA setup)" under the row) and Amazon's ACCEPTED/INVALID response decides. Safety-question handling is unchanged.

Live read-only check on the Pi after deploy: UV-SO4Z-K3CC, 2M-RBXO-SHCC, 63-0FRS-5M1I read `needs_enablement` with notes; A1-GX7S-49LJ and RC-LULE-P5B5 now carry FNSKUs and read `ready`. Deployed sweetshelves/fba_readiness.py, sweetshelves/fba_inventory.py, templates/fba_prep.html (Pi backups in /tmp/fba-offer-fix-20260909); service active, /fba-prep 200 with the new template. Desktop debby received identical function edits in app.py, the template, and four new tests. Validation: 221 modular tests with only the 4 known signature-snapshot failures; 83 inline scripts parse; FBA JS suites pass (Playwright suites unavailable locally). Desktop test_fba_count_scan: the new tests pass; five older scan tests there fail independently of this change (they mock readiness and the monolith scan flow has diverged). No Amazon listing was modified during verification; the user has not yet clicked Set up on the deployed build.

### Git update preference (2026-09-09)

After every verified fix: deploy to the Pi, sync the Desktop `debby` checkout, and also commit the changed files on `codex/main-working-project` and push to `origin` (github.com/danka-on/simpleInventory). The user asked for this to always happen; it extends the automatic deployment preference above. Commit only the files relevant to the fix; never add `.db` files.
