# Listing Agent redesign

The listing workspace is implemented locally. It keeps the existing Flask/eBay/Amazon endpoints and moves the real controls, preserving attached event handlers, photo ordering, warehouse checks, catalog matching, AI helpers, saved defaults, and listing/edit actions.

## Primary workflow: similar listing prefill

The main action is now **Find a similar listing** at the top of the editor. Selecting an inventory item starts a background barcode search with product-title fallback. Manual product names are respected instead of being replaced by the active UPC. Live Browse results are searched before catalog fallback, ranked for title/model relevance, and kept separate by listing ID so different variants do not have their specifics merged.

Choose **Use this listing** to populate title, category, available item specifics, and a suggested asking price. The selected listing's full details are fetched for additional specifics. An empty or previously generated description is built from structured product facts and your condition notes; authored description text remains intact. Your own photos, quantity, condition, and policies remain in place. The match status reports whether full details loaded or only summary data was available.

Search and detail responses are checked against the active item and newest request. Late details respect manual edits. Programmatic prefill now schedules draft saving.

Additional verification: `python test_listing_similar.py` (4 backend tests) and `node test_listing_similar.cjs` cover Browse-first behavior, title fallback, separate variants, relevance, partial outages, manual queries, stale responses, and protected detail filling. An isolated browser run confirmed a selected match fills 6 specifics, changes the price, and builds a description. Live authenticated eBay calls remain untested.

## Workflow

1. Find an item by title or barcode in the queue. Alt+Q focuses the queue filter; Enter opens a focused item.
2. Review item/photos and edit title, category, and condition together.
3. Complete category-specific attributes, then price/quantity, description, and shipping policies.
4. Use the review panel to inspect the outgoing title, price, quantity, condition, and main image. Click a missing detail, or Next, to reach it directly.
5. Save draft with the button or Ctrl/Cmd+S, or submit using the existing Create Listing action. Server validation remains authoritative.

The desktop layout has queue/editor/review columns. Smaller screens stack the sections. Existing eBay and Amazon publishing handlers remain in use. The separate phone photo-upload page is unchanged.

Save failures now remain visibly unsaved and prevent switching queue items. Edits made during a save stay dirty. A browser leave-page prompt protects unsaved work. This does not provide a durable offline draft store.

## eBay capability assessment

Much of eBay's listing functionality can be integrated, but this redesign is not complete Seller Hub parity. The app currently builds single-SKU fixed-price offers. API availability, marketplace, category, seller eligibility, and account permissions affect feature support.

| Capability | Current workspace / remaining work |
| --- | --- |
| Product title, description, identifiers, category, condition | Existing integration retained; reorganized editor |
| Photos, order, selected images, phone uploads | Existing integration retained; main photo shown in review |
| Category item specifics | Existing taxonomy and catalog tools retained; missing required values linked in review |
| Fixed price, quantity, listing/edit submission | Existing integration retained; stricter local validation |
| Shipping, payment, return policies | Existing saved-policy selection retained; labels simplified |
| Working drafts and reusable defaults | Existing server storage retained; save status and failure handling corrected |
| Best Offer and automatic accept/decline | Not exposed by the current offer builder; needs payload, draft persistence, and validation work |
| Auctions and scheduling | Not implemented in this listing flow; needs appropriate API integration and format-specific validation |
| Variations | Needs inventory groups, per-variation SKUs/stock/prices/images, category support checks, and publish/update logic |
| Video and advanced photo editing | Needs media upload/status and editing workflows |
| Promotions, volume discounts, offers to buyers | Needs marketing/negotiation integration and seller eligibility checks |
| Shipping overrides, package weight/dimensions, international options | Needs supported offer/inventory fields and account-policy integration beyond selecting saved policies |
| Store categories, compatibility, regulatory details | Needs category/marketplace-dependent controls and payload support |
| Expected fees / proceeds | Needs fee lookup and trustworthy cost inputs; the review does not invent a net-profit figure |

User priority is similar-listing discovery and prefill. Advanced selling options are lower priority.

Official sources checked September 7, 2026:

- [eBay listing tools](https://www.ebay.com/help/listings/creating-managing-listings/creating-managing-listings?id=4105)
- [Inventory and offer capabilities](https://developer.ebay.com/api-docs/sell/static/inventory/managing-inventory-and-offers.html)
- [Listing creation and inventory groups](https://www.developer.ebay.com/develop/guides/sell/listing-creation)

## Verification and limits

`node test_listing_workspace.cjs` checks both scripts parse, 13 invalid eBay/Amazon cases, draft success/failure, and edits during a save.

Browser checks against an isolated preview cover desktop and 390px layouts, queue filtering, navigation to price, live price updates, explicit saving, simulated failed saving, marketplace switching, and empty state. No live listing was published and no production databases were used. Authenticated API round trips, actual photo uploads, and Pi deployment remain untested.

Run `python tools/preview_listing_workspace.py` from the project directory for sample data at http://127.0.0.1:8765. Add `?empty=1` for the empty state or `?save_error=1` for a failed save. The fixture rejects publishing and never imports the production Flask app.

## Native eBay recommendations

The workspace now calls eBay Inventory Mapping directly: a background GraphQL task produces a preview, the user reviews and applies it, and the recommendation reference is saved with the working draft. Only `PREFILL` confidence values are applied automatically; `HINT` values are displayed for verification. Newer edits are preserved. Polling pauses after ten minutes and can be resumed; completed previews are cached on the server. Descriptions are sanitized before rendering.

Supported scope is new, single-SKU, US fixed-price listings. eBay currently excludes Motors and Parts & Accessories. Existing Inventory API offers retain their original edit flow. Similar-listing search remains available when native recommendations are unavailable.

Mapped drafts publish through Trading `VerifyAddFixedPriceItem` then `AddFixedPriceItem`, carrying `MappingReferenceId`. Publication records, a stable UUID and SKU tracking prevent blind duplicate creation; uncertain requests are retained for reconciliation. There is no fallback that drops the reference. Locally saved drafts remain available without publishing.

Setup on the server: reauthorize with `ebay_oauth_setup.py` to grant `https://api.ebay.com/oauth/api_scope/sell.inventory.mapping`; if `EBAY_SCOPE` is explicitly configured, include that scope there too. Refreshing an old grant alone cannot add permission. The existing `EBAY_OLDAUTH_TOKEN` Trading connection is required for mapped publishing. Seller business policies and a US inventory location are reused. Photo URLs must be publicly accessible over HTTPS for mapping; unusable URLs are omitted while title/barcode/details remain usable. Account API eligibility must be verified live.

`python test_ebay_mapping.py` covers GraphQL variables and error handling, partial results/confidence, HTML sanitization, reference transmission, validation, cached tasks, cross-item reference rejection, and duplicate/uncertain publishing. Browser fixture checks cover applying a preview and preserving newer title edits. No authenticated eBay calls or live publishing were performed.

Official references: [Inventory Mapping schema](https://developer.ebay.com/develop/api/sell/inventory_mapping), [listing creation](https://developer.ebay.com/develop/guides/sell/listing-creation), [AddFixedPriceItem reference and UUID](https://developer.ebay.com/devzone/xml/docs/Reference/ebay/AddFixedPriceItem.html).

Deploy `app.py`, `ebay_mapping.py`, `listing_mapping_routes.py`, `token_manager.py`, `ebay_oauth_setup.py`, `templates/listingagent.html`, `static/listing-workspace.css`, `static/listing-workspace.js`, and `static/listing-mapping.js`; restart the existing service. The new tables are created lazily in the server's `listagent.db`. Do not deploy the preview, tests, tokens, or database files.
