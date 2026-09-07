# Application modularization — September 7, 2026

The application has been split into feature modules. `app.py` went from 60,335 lines to a 30-line entrypoint. The `sweetshelves` package contains 86 feature/core modules and four supporting files for package initialization, startup, route registration, and legacy exports. The largest feature module is 2,493 lines.

Inventory, preparation, listing, eBay, Amazon, FBA, sales, shipping, BOL, mail, and maintenance operations now have separate source files. Shared configuration, Flask/cache instances, database connections, errors, normalization, and worker startup have explicit owners. Existing scripts can still import the original helpers from `app`.

The extraction uses ordinary Python modules and explicit imports. It does not execute source fragments or rebind function globals. All 471 Flask URL rules retain their ordering, methods, defaults, and endpoint names. Template/static/data paths remain rooted at the repository directory. The existing Gunicorn and local startup commands continue to use `app.py`.

The original database operations and transaction boundaries remain intact. This is a structural refactor; it does not claim a runtime performance improvement. Some long operations and mutual feature dependencies remain visible in their new modules, where they can be changed and tested independently.

## Validation

- **145 Python tests passed**, including the original 133 tests and 12 architecture/startup checks.
- **All nine JavaScript/browser suites passed.**
- **83 JavaScript files/inline blocks**, **160 Python files**, **71 Jinja templates**, and **seven PowerShell scripts** passed syntax checks. Python sources also parse using Python 3.11 grammar.
- **All 1,132 original functions and the helper class passed a source-structure comparison** against the captured pre-refactor application. The comparison normalizes qualified module references and relocated Flask decorators; function logic is unchanged.
- Exact route/signature contracts, hook ordering, upload errors, connection teardown, project-root paths, legacy imports, and representative page rendering passed.
- Fresh-process checks confirmed feature imports do not open SQLite connections, connect to network services, or start background threads. Repeated initialization does not re-register routes or start workers.
- The deployment script passed a mock-transfer execution: the complete Python package transfers before the entrypoint, a planted non-code database sentinel is excluded, and staging is removed. SCP/SSH were stubbed; no network transfer occurred.
- Static analysis found no undefined names in the extracted application. Existing unused-local/import and formatting advisories remain.

Tests ran against disposable copies with background services disabled. Live marketplace APIs, printers, Cloudflare Access, and the Pi service were not exercised. Nothing has been deployed in this task.

See [architecture.md](architecture.md) for module ownership and extension guidance, [modularization-test-results.txt](modularization-test-results.txt) for suite output, and [modularization-static-results.json](modularization-static-results.json) for source/syntax counts.

The pre-refactor source snapshot is retained locally at `C:\Users\boxatron\AppData\Local\Temp\sweetshelves-modular-baseline-zqvtgv2g\app.py`. This is the working-tree version captured immediately before extraction, including the preceding audit fixes.

## Direct Pi deployment commands

These transfer this refactor's package and entrypoint. The package must be deployed with `app.py`; copying only the entrypoint would leave its imports unavailable. The local package currently contains only Python source. The updated `deploy-to-pi.ps1` additionally stages a source-only copy and transfers the full root-adapter/template/static allowlist if the preceding changes also need deployment.

```powershell
Set-Location -LiteralPath C:\Users\boxatron\Documents\ChatGPT\sweetshelves\push-small-parcel-fix
scp -r -i C:/Users/boxatron/.ssh/sweet_shelves_pi .\sweetshelves dk@10.0.0.151:/opt/sweetshelves/
scp -i C:/Users/boxatron/.ssh/sweet_shelves_pi .\app.py dk@10.0.0.151:/opt/sweetshelves/
ssh -i C:/Users/boxatron/.ssh/sweet_shelves_pi dk@10.0.0.151 "sudo systemctl restart sweetshelves.service"
```

No database files are part of this transfer. After deployment, confirm the service starts and check Finder, receiving, listing, FBA scans, and Ready to Ship on the Pi before relying on the refactored build for warehouse work.
