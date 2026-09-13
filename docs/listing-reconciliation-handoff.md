# Listing reconciliation — September 13, 2026

## Expand local matches — deployed and verified

Listings initially show three candidates and offer **Show 3 more matches** when additional qualifying products exist. Each click appends up to three candidates to that listing without a full reconciliation refresh, preserving earlier matches and checkbox selections through expansion and filtering. On exhaustion the button is replaced by **No more matching products found locally.** Failures preserve results and allow retry. Claude results and expanded candidates remain visible together.

The new `more_matches` action on the existing POST API performs local matching only. It reloads active listings, stocked rows and user decisions, then ranks candidates using the same scoring and sister-listing evidence. Previously shown IDs and product barcodes are excluded; padding, numeric unit suffixes and duplicate locations do not generate another copy of the same product. Invalid requests and resolved listings are rejected. It never calls Claude, changes inventory or creates links.

56 focused Python checks passed, including multi-page exhaustion, distinct products, stale stock, invalid input and no Claude calls. Browser checks passed for 3→6→9→10 matches, retry, selection retention, filter retention, and no full refresh; all 90 JavaScript blocks parsed. Main and Desktop code/templates are synchronized. Deployed hashes match and service is active. Live expansion for **1 x Chilewich Mosaic Rectangle Placemat** returned three different additional candidates in 0.91 seconds, with more remaining. Backups: `/tmp/reconciliation-before-more-20260913/`. No databases transferred or live links changed.

## Whole-warehouse Claude text search — deployed and verified

Replaced AI photo comparison at the user's request. Unresolved listings now have **Search whole warehouse with Claude**, even if local matching found nothing or photos are missing. Every stocked row is supplied as text with barcode, current title, saved custom/confirmed names, catalog title and warehouse note. No local shortlist is used. Brand, type, color and piece count guide semantic matching; Claude can return up to five likely/possible suggestions or no match. Existing local suggestions remain available and no AI result changes stock, links, or accounted state. Clickable photo enlargement remains.

Uses `reconciliation_ai.py` with the existing Haiku 4.5 configuration. A free token-count check precedes generation; catalogs above 180,000 input tokens stop without a paid search instead of silently searching only part. Responses require valid unique stocked IDs, rationale and a completed response. Requests are concurrency-limited, time-bounded and only sent on the button's POST action `search_warehouse`. The old `compare_photos` action is rejected. Catalog/listing changes are checked before returning suggestions; manual Link remains the existing validated endpoint.

Full results, including no-match answers, persist for seven days in `reconciliation_text_cache` in `listing_alerts.db`. The key includes model/version, listing identity/text, and the entire stocked catalog's IDs/barcodes/names/notes. Adding/removing stock or changing names invalidates the result. Changes to quantities that remain positive or locations do not change matching text; candidate stock/location is loaded fresh. Regular GET/search never invokes Claude. The page shows an estimated price range before searching and the original request cost estimate afterward. Prices use [Anthropic's published Haiku 4.5 rates](https://platform.claude.com/docs/en/about-claude/pricing): $1/million input tokens and $5/million output tokens, verified September 13, 2026.

Live verification searched all 1,591 stocked rows for Amazon SKU `L5-B2XL-5N2L`, the 13-inch rattan charger set. It found rack row 4072, **The Jay Companies Round Rattan Brown 13 Inch Charger Set of 4**, which was absent from the original three local suggestions. This is still an unconfirmed suggestion. Generation plus reconciliation checks took 13.30 seconds and token usage implies $0.076751. Repeating used the local result cache (no new paid generation; 10.38 seconds for fresh inventory checks). A fresh GET retained the evidence and the listing remained suggested. No inventory links, quantities or marketplace listings were changed.

Validation: 54 focused Python tests passed, then 32 reconciliation/AI tests passed after adding post-request listing checks; the seven standalone engine tests also pass in Desktop. Browser checks verified the new action, price/result display, no full-page reload, photo enlargement, linking and multi-select behavior. All 90 JavaScript blocks parse. Deployed engine, module and template hashes match; service active. Desktop sources/tests are synchronized. The old photo engine/tests were removed locally and in Desktop, and the deployed engine was removed on the Pi. Historical photo cache rows are unused, with no destructive database migration. Backups: `/tmp/reconciliation-before-claude-text-20260913/`. No routing change or database transfer was required.

## Earlier fuzzy photo comparison — superseded and removed

The following records the earlier deployment, before the whole-warehouse text search replaced it. Photo processing is no longer available.

Added an on-demand Compare photos action. It sends the listing photo plus up to three candidate photos to Anthropic Claude Haiku using the existing API configuration. The prompt requests semantic comparison tolerant of angle, lighting, background, packaging and crop differences, using visible shape, pattern, markings and color. It returns similar/possible/different/unclear explanations. Ranking combines 75% existing text score and 25% visual evidence; unclear evidence leaves text rank unchanged. Scores are ranking heuristics, not calibrated accuracy probabilities. No image verdict creates a warehouse link or changes the accounted state.

Unchanged results persist for seven days in `listing_alerts.db` keyed by listing/candidate identities, text, image URLs, model and scoring version. Regular searches read cached evidence only and never call the paid API. Comparisons are limited to one concurrent request; the service timeout is bounded. Server-side listing data chooses images, not client-submitted URLs. Local saved photos are resolved under static, size-limited and resized before transmission. The implementation follows [Anthropic's vision documentation](https://platform.claude.com/docs/en/build-with-claude/vision).

54 focused Python tests and browser checks passed, including cache reuse/expiry/invalidation, malformed results, unclear evidence, missing configuration, service failures, concurrency bounds, safe local paths, authoritative input selection, no auto-linking, and UI updates without a full reconciliation refresh. All 90 scripts parsed. Additional source-loading tests passed after the live integration fix (10 vision tests total). Desktop source is synchronized. The user explicitly approved sending listing/warehouse photos to Anthropic, and the feature is deployed on the Pi.

The first live request exposed a provider download failure. Supported Amazon/eBay CDN photos are now downloaded on the Pi with bounded size/time and no redirects, then resized to at most 1024 pixels before transmission. Absolute app-host static photos resolve to the local static directory, avoiding its external access gate. A live comparison of the Amazon rattan charger listing completed in 5 seconds, identified differing materials/patterns in all three text candidates, and adjusted their ranking scores. Repeating it returned from cache immediately, and evidence survived a fresh reconciliation read. The listing remained suggested; no links or stock were changed.

The Pi has newer unmatched-prep routes absent from the local routing file. The deployed `tools/routing-with-vision.py` preserved a freshly downloaded production routing file and changed only reconciliation from GET to GET/POST. Refresh that snapshot for future deployments rather than overwriting newer routes. `reconciliation_vision.py`, the feature module/template, and the carefully merged routing are deployed. No databases were transferred. Pre-deployment backups are in `/tmp/reconciliation-before-vision-20260913/`. The regular search/brand/photo-popup/batch-link features from earlier updates remain deployed.

## Search and brand-ranking update

Search now supports reordered words, accent normalization, small name typos, exact padded/suffixed identifiers, pasted Amazon/eBay links, matched warehouse locations/notes, and saved custom or learned names attached to current stock. Search all tabs is enabled for nonempty queries and can be turned off. A brand filter lists recognized active brands by frequency. Brand recognition includes 59 brands selected from recurring real catalog prefixes; vocabulary and spelling aliases live in `reconciliation_names.py` and can be extended there.

Following the user's guidance, suggestions weight brand (0.40), product type (0.25), color (0.15), and piece count (0.08), alongside name similarity (0.12). Only available query attributes contribute to the denominator. Missing candidate details remain uncertain; known mismatches reduce rank. Related types such as cups/mugs remain plausible. Product-type differences are a soft penalty, superseding the earlier absolute type exclusion. Confidence caps prevent weak names or conflicting counts/sizes from overriding missing-stock history. These weights rank suggestions only and never auto-link by name. Barcode/manual-link accounting remains unchanged.

Confirmed custom names enrich stocked rows, including suffix units. A saved custom-name record without stock still cannot become an available warehouse match. Query and candidate brand spellings share aliases, including Yinka Lori / Yinka Ilori. Same-brand stock remains searchable even when the brand is too common for rare-word matching.

Validation: 44 focused Python checks passed, including ordering by brand/type/color/pieces, missing-brand tolerance, count parsing, common-brand candidate retrieval, custom names, and stock integrity. Browser checks passed for reordered words, name typos, saved names, padded barcodes, wrong-barcode rejection, pasted URLs, locations, all-tab/current-tab scope, brand filtering/aliases, image previews, and batch linking. All 90 scripts parsed. Synced the Desktop module, vocabulary, template, and browser test. Deployed the vocabulary before the feature module and template; hashes match and service is active. The read-only live smoke check accounted for all 1,783 active listings and took 10 seconds. Live manual links changed during development; changes to the accounted total are not attributed to automatic name matching.

For future deployments, upload `reconciliation_names.py` to `/opt/sweetshelves/` before the feature module. It has been added to the modular deployment script. Pre-update module/template backups are at `/tmp/reconciliation-before-brand-ranking-20260913`.

## Batch linking update

Removed the introductory paragraph at the user's request. Suggestions now include Select checkboxes and a sticky Link selected button. Only one candidate per listing can be selected; selections survive filtering and pagination. The batch saves each selected link through the existing validated endpoint, then refreshes reconciliation once. Successful links leave the selection; failures remain selected with an error summary for retry. Controls are disabled while saving to prevent overlapping actions. Individual Link remains available.

Verified in headless Edge: selecting multiple listings, replacing one listing's candidate, retaining selections through filters, exactly one refresh per batch, partial success, retrying only failures, and the existing page actions/mobile layout. All 89 scripts parse. Deployed the template, verified the live controls and service, and synchronized the Desktop template and browser test. Previous template: `/tmp/listing-reconciliation-before-bulk-20260913.html` on the Pi. Deployment commands below remain applicable; this update required only the template SCP and service restart.

Open **Store Manager → Listing Reconciliation** (`/listing-reconciliation`). The page includes every saved active eBay listing and merchant-fulfilled Amazon listing; unsold/inactive and Amazon-fulfilled inventory are excluded. It uses the stores' existing sync databases, not a fresh marketplace API sync.

## Completed handoff

Finished Claude's module and page. Barcode matches account for zero padding and numeric unit suffixes, including stock with both an exact barcode and suffixed rows. Arbitrary alphanumeric warehouse identifiers and numbers embedded in alphanumeric SKUs are not silently converted into UPC matches. Title and shared-prefix candidates remain suggestions requiring physical verification and an explicit Link action. Duplicate locations do not consume multiple suggestion slots.

Manual links use the existing listing inventory match table and Finder alias learning. A deleted, empty, or reused warehouse row cannot count as accounted. Unlink removes its learned listing alias through the existing endpoint. Handled listings remain in the coverage denominator; dismissing a gap does not create warehouse stock. Failed store/warehouse reads return an error instead of successful incomplete coverage.

“Likely sold out” means the code appears in receiving or archive history but has no matched stock. It is not proof of a sale. Links and dismissals stay local; this page does not end listings, push UPCs, reserve shared stock, or change warehouse quantities. Short stock is a per-listing comparison; cross-store offers can reference the same stock.

## Verified production result

Deployed only `sweetshelves/listing_reconciliation.py` and `templates/listing_reconciliation.html`. Existing routing, legacy exports, and Store Manager navigation already matched the local files by SHA-256. Service restarted successfully; deployed file hashes match local files. Previous versions are retained at `/tmp/reconciliation-codex-20260913` on the Pi.

Read-only smoke check verified the full result against independent active-row counts in the live SQLite databases:

| Measure | Listings |
| --- | ---: |
| Active eBay | 1,214 |
| Active Amazon merchant-fulfilled | 569 |
| Total | 1,783 |
| Accounted | 974 |
| Needs a look | 271 |
| Likely sold out | 335 |
| Unaccounted | 203 |
| Accounted listings with short stock | 33 |

Coverage is 54.6%. The check, including API generation and page/navigation checks, took 5.88 seconds. No live links, dismissals, inventory changes, or marketplace updates were performed during verification. These counts are a deployment-time snapshot.

The Desktop `simpleInventory` checkout has the equivalent standalone module with injected dependencies, registration in its monolithic app, the page, and navigation. Its isolated API, suffix match, template rendering, and app syntax checks passed. Unrelated working-tree changes were preserved; no Git commit was created.

## Validation

- 39 focused Python checks passed: reconciliation, Finder match learning, Ready to Ship suffix handling, and the exact route contract.
- Headless browser checks passed: store/search filters, text escaping, Finder handoff, unavailable-link rejection and retry, Link, Unlink, Mark handled, Bring back, honest totals, and 390px mobile layout.
- All 89 JavaScript blocks passed syntax checks.
- The complete JavaScript/browser suite also passed using the bundled Playwright dependency and installed Edge browser.
- The broader 370-test Python run exposed the missing reconciliation routes in the contract (fixed and retested), plus four pre-existing FBA signature assertions. They remain unresolved: `_amazon_resolve_asin_from_upc`, `_fba_local_amazon_listing`, `_fba_local_amazon_listing_by_asin`, `_fba_amazon_box_label_document`. Their implementations were not changed by this work.

Run focused checks in disposable databases:

```powershell
py -3.13 tools/run_checks.py test_listing_reconciliation test_finder_match_learning test_ready_to_ship_suffix_matching test_application_architecture.ApplicationArchitectureTests.test_every_existing_route_preserves_order_endpoint_and_methods
```

The browser suite is `test_listing_reconciliation.cjs` and uses the installed Edge browser with Playwright. The Pi smoke script is `tools/verify_reconciliation.py`.

## Repeat deployment

Already deployed and verified; these commands are provided for the next relevant update. Run from the main modular checkout. Never transfer databases.

```powershell
scp -i C:/Users/boxatron/.ssh/sweet_shelves_pi reconciliation_names.py reconciliation_ai.py dk@10.0.0.151:/opt/sweetshelves/
scp -i C:/Users/boxatron/.ssh/sweet_shelves_pi sweetshelves/listing_reconciliation.py dk@10.0.0.151:/opt/sweetshelves/sweetshelves/
scp -i C:/Users/boxatron/.ssh/sweet_shelves_pi templates/listing_reconciliation.html dk@10.0.0.151:/opt/sweetshelves/templates/
ssh -i C:/Users/boxatron/.ssh/sweet_shelves_pi dk@10.0.0.151 "sudo systemctl restart sweetshelves.service"
```
