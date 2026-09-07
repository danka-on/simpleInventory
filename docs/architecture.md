# Sweet Shelves application structure

`app.py` is the 30-line WSGI and local-development entrypoint. Application code lives in the `sweetshelves` package. This replaces the 60,335-line module while preserving the existing URLs, endpoint names, database layout, templates, and startup commands.

The package has 86 feature/core modules and four supporting files: `__init__.py`, `bootstrap.py`, `routing.py`, and `legacy.py`. The largest feature module is about 2,500 lines. Some individual operations remain long; their transaction boundaries and behavior were deliberately retained during extraction.

## Startup and shared ownership

1. `config.py` resolves the repository root and reads its `.env`. Moving code into the package does not relocate databases, configuration, uploads, templates, or static assets.
2. `runtime.py` constructs the singleton Flask application, Flask-Caching instance, and compression extension. Flask's root remains the repository directory.
3. Feature modules define their functions, constants, caches, and worker flags. Importing them does not initialize databases, connect to a network service, or start workers.
4. `bootstrap.py` registers routes, runs the existing startup schema/index initialization, loads the eBay token, and schedules background services unless `DISABLE_BACKGROUND_SERVICES` disables them. Repeated `initialize()` calls reuse the initialized application.
5. `app.py` exports `app` for Gunicorn. Running it directly starts the local server and optional tunnel. `SWEETSHELVES_NO_TUNNEL=1` remains supported.

| Shared concern | Owner |
| --- | --- |
| `BASE_DIR`, environment settings, application logger | `config.py` |
| Flask `app`, shared response `cache` | `runtime.py` |
| Database connection/transaction helpers, teardown, WAL/index setup, SQL identifier quoting | `database.py` |
| Safe client errors, debug guard, listing user errors, upload-size handler | `errors.py` |
| Barcode/quantity/date normalization | `normalization.py` |
| Data-version and inventory cache invalidation | `caching.py` |
| Cache settings and scheduler | `cache_admin.py` |
| Response headers and template context | `web_hooks.py` |
| Optional Amazon/BOL availability and imports | `integrations.py` |
| Worker startup lock and delayed startup | `background.py` |
| Local Flask server and Cloudflare tunnel | `server.py` |

Shared state has one owner. For example, read or patch `config.BASE_DIR`, `runtime.cache`, or `fba_readiness._FBA_LISTING_READINESS_CACHE`; importing a value into another module and assigning a replacement there does not update its owner.

## Feature map

All filenames below are inside `sweetshelves/`.

| Area | Modules |
| --- | --- |
| Receiving, identity, finding stock | `warehouse_receiving.py`, `warehouse_matching.py`, `warehouse_allocations.py`, `warehouse_search.py`, `finder.py` |
| Shelves, locations, maps | `warehouse_locations.py`, `warehouse_maps.py`, `shelves.py`, `shelf_assets.py` |
| Inventory history and aging | `inventory_history.py`, `inventory_history_views.py`, `inventory_age.py`, `inventory_age_evidence.py`, `inventory_age_rescan.py` |
| Inventory maintenance | `inventory_cleanup.py`, `inventory_archives.py`, `inventory_alerts.py`, `enrichment.py` |
| Item preparation | `prep_schema.py`, `prep_context.py`, `prep_actions.py`, `prep_undo.py`, `prep_log.py`, `prep_views.py`, `prep_diagnostics.py`, `prep_locations.py`, `prep_media.py` |
| BOL and bulk manifests | `raw_bol.py`, `bol_inventory.py`, `bulk_manifest.py`, `manifest_google.py` |
| Listing workflow | `listing_settings.py`, `listing_checks.py`, `listing_queue.py`, `listing_log.py`, `listing_lifecycle.py`, `listing_alerts.py` |
| eBay | `ebay_auth.py`, `ebay_catalog.py`, `ebay_policies.py`, `ebay_publish.py`, `ebay_orders.py` |
| Amazon | `amazon_catalog.py`, `amazon_listing.py`, `amazon_publish.py`, `amazon_orders.py` |
| FBA | `fba_schema.py`, `fba_inventory.py`, `fba_readiness.py`, `fba_sessions.py`, `fba_shipments.py`, `fba_scanning.py` |
| Sales and shipping | `sales.py`, `sales_actions.py`, `sales_repair.py`, `shipping_identity.py`, `shipping_orders.py`, `shipping_labels.py` |
| Other marketplaces and finance | `marketplace_sales.py`, `marketplace_removal.py`, `facebook.py`, `pricing.py`, `payouts.py`, `returns.py`, `analytics.py` |
| Mail, notifications, and system tools | `mail_schema.py`, `mail_center.py`, `email_settings.py`, `telegram.py`, `health.py`, `server_metrics.py`, `printing.py`, `sync.py`, `diagnostics.py`, `pages.py` |

Existing standalone adapters remain at the repository root: `DBmanager.py`, `amazon_manager.py`, `ebay_manager.py`, `rawbol_manager.py`, `marketplace_manager.py`, `printer_manager.py`, `token_manager.py`, and the other supporting libraries. They were not duplicated into the package.

## Adding or changing a feature

Put the implementation in its feature module. Use ordinary imports to reference dependencies, for example:

```python
from . import database as ss_database
from . import errors as ss_errors

def api_example():
    ...
```

Register the endpoint explicitly in `routing.register_routes()`. That function keeps existing endpoints unprefixed and preserves registration order, including stacked routes, cache/debug decorators, hooks, upload errors, and the eBay mapping registrations. Route functions keep their original names for `url_for()` and existing callers.

Keep database initialization and worker startup in `bootstrap.py` or an explicitly called feature initializer. Avoid calls that read or write databases at module scope. Declare feature-owned caches and locks in the same module as their implementation. Cross-feature calls use module attributes, so mocks and mutable state have a clear owner. Some features still have mutual dependencies inherited from the original application; keep those lookups inside functions and move shared utilities into a core owner instead of introducing eager cross-feature calls.

`legacy.py` supports existing scripts using `from app import some_helper`. It resolves helpers to their real modules; it does not copy or execute source, rebind function globals, or mirror mutable state. New application code must import feature modules directly. Assignment to an attribute on the legacy entrypoint is not a supported way to configure the application.

## Verification

Run the offline suite from the repository directory:

```powershell
py -3.13 tools/run_checks.py --javascript
```

The runner copies Python code, the package, test fixtures, templates, and JavaScript/CSS into a disposable directory. It excludes live databases, credentials, and `.env`; disables workers/tunnels; and removes the disposable copy when complete. Browser tests require the existing Playwright/Edge test environment. Without `--javascript`, only Python tests run.

`test_application_architecture.py` checks the captured route/signature contract, hook order, legacy imports, internal symbol ownership, import side effects, idempotent startup, Flask paths, request connection teardown, upload errors, and representative page rendering. Existing feature tests now call imported production functions and patch their owners, rather than extracting function bodies from `app.py`.

`tests/fixtures/application_contract.json` records the routes and signatures carried forward from the monolithic application. Following the Desktop merge, it contains 473 routes and 1,153 function signatures, including the two routes and 21 functions added on that branch. All original 471 route records and 1,132 signatures remain unchanged. Deliberate URL or signature changes should update the corresponding contract entries in the same change, with tests for the new behavior. Function bodies are not frozen by this fixture.

## Deployment

Deploy the complete `sweetshelves` package together with `app.py`. The updated `deploy-to-pi.ps1` stages only package `.py` files, transfers the package first, checks transfer failures, and cleans its temporary staging directory. It continues to transfer the root adapters, templates, and static assets.

No database migration or data transfer is part of this refactor. See `modularization-2026-09-07.md` for validation results and direct Pi transfer commands.
