# Main working project and Desktop reconciliation

Continue using `C:\Users\boxatron\Documents\ChatGPT\sweetshelves\push-small-parcel-fix` on `codex/main-working-project`. The project folder is unchanged. It is a linked worktree sharing the Git repository in `C:\Users\boxatron\Desktop\simpleInventory`; this operation does not create or push a GitHub repository.

The main branch combines two saved versions with a real two-parent Git merge:

| Saved version | Branch | Commit |
| --- | --- | --- |
| Desktop, before overhaul | `debby` | `4bff175a6846115c0e7b4dbedba5c48f40892b34` |
| Audited and modularized Documents application | `codex/codebase-overhaul` | `24c08d04d84aa6a12f0d07fddf75e47618890d90` |

Their common ancestor is `7eee97dadc56dda09b3ce8ab7e6b1df82af4e205`. The Desktop version had changes missing from Documents, while Documents had its own later work. Neither folder was treated as a complete replacement for the other. Both saved branches remain available as recovery checkpoints.

## What the merge preserves

- The audit fixes, 30-line entrypoint, and 90-file application package from Documents.
- Desktop FBA preparation checks, package measurements, recovery of Amazon plans with saved carton scans, moving scans between cartons, and item lookup.
- Desktop token-expiry status/reminders and preservation of OAuth refresh-token expiry metadata. `token_expiry.py` is included in the deployment allowlist.
- The parcel-only transportation rules and the requirement that every selected destination has a purchasable quote before buying transportation.
- Receiving metadata resolution together with explicit failure handling when inventory cannot be saved. A direct SQLite connection now closes on failure as well as success.

Overlapping Python logic was reconciled against a captured monolithic source before being assigned to the existing feature modules. Overlapping template and test changes were reviewed together. The merge was assembled and tested in a temporary worktree, leaving the Desktop checkpoint and existing Documents working files in place.

## Validation

- **173 Python tests passed.** These cover the combined feature tests, architecture contracts, transactions, token expiry, and receiving conflict regressions.
- **All 10 JavaScript/browser suites passed**, including a new FBA transport test for missing destination quotes and purchase gating.
- **83 JavaScript scripts/inline blocks parsed successfully.**
- The complete route/signature contract passed: **473 routes and 1,153 function signatures**. All original 471 route records and 1,132 signatures remain unchanged; the Desktop branch adds two routes and 21 functions.
- All **1,154 definitions** passed an independent normalized AST comparison against the reviewed monolithic merge. **163 Python files** passed syntax checks using Python 3.11 grammar, **71 Jinja templates** parsed, and the PowerShell scripts parsed without errors.
- Static analysis found no unresolved names or use-before-assignment errors in the application package and changed root adapters. Existing unused-import/local and formatting advisories remain.

See [test output](merged-project-test-results.txt) and [static-check counts](merged-project-static-results.json). Tests use disposable databases, disabled background services, and mocked marketplace calls. Live Amazon/eBay transactions, printers, Cloudflare Access, and the Pi service were not exercised. The passing suite is evidence for the tested behavior, not a guarantee that every production path is covered.

## Incremental Pi deployment after the earlier overhaul

The user reported running the earlier Pi commands. The following additional transfer installs this merged revision; it has not been run by the agent. It assumes the package directory from that deployment exists. Only Python source and the two changed templates are transferred. Existing Pi databases, credentials, and configuration are retained.

```powershell
Set-Location -LiteralPath C:\Users\boxatron\Documents\ChatGPT\sweetshelves\push-small-parcel-fix
scp -i C:/Users/boxatron/.ssh/sweet_shelves_pi .\DBmanager.py .\fba_inbound.py .\token_expiry.py .\token_manager.py .\ebay_oauth_setup.py dk@10.0.0.151:/opt/sweetshelves/
if ($LASTEXITCODE -ne 0) { throw 'Python adapter transfer failed' }
scp -i C:/Users/boxatron/.ssh/sweet_shelves_pi .\sweetshelves\*.py dk@10.0.0.151:/opt/sweetshelves/sweetshelves/
if ($LASTEXITCODE -ne 0) { throw 'Application package transfer failed' }
scp -i C:/Users/boxatron/.ssh/sweet_shelves_pi .\app.py dk@10.0.0.151:/opt/sweetshelves/
if ($LASTEXITCODE -ne 0) { throw 'Entrypoint transfer failed' }
scp -i C:/Users/boxatron/.ssh/sweet_shelves_pi .\templates\fba_prep.html .\templates\fba_labels.html dk@10.0.0.151:/opt/sweetshelves/templates/
if ($LASTEXITCODE -ne 0) { throw 'Template transfer failed' }
ssh -i C:/Users/boxatron/.ssh/sweet_shelves_pi dk@10.0.0.151 "sudo systemctl restart sweetshelves.service"
```

After deploying, confirm service startup and the receiving, Finder, listing, FBA scanning/transport, and Ready to Ship flows on the Pi. Do not update the deployment-success checkpoint until the user confirms success.
