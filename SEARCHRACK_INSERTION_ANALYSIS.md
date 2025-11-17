"""
SEARCHRACK.DB INSERTION ANALYSIS
=================================

This document details ALL circumstances where values are added to searchRack.db,
EXCLUDING the "add to shelf" manual flow.

================================================================================
1. ITEM PREP FLOW - Marking items as "done"
================================================================================
Location: app.py lines 3200-3260
Trigger: POST /api/items_prep/update_status

When a user marks an item as "done" in the item prep system and provides a 
location (shelf code) or picture position:

BEHAVIOR:
- Checks if barcode already exists in SEARCHRACK
- If EXISTS: Updates the existing entry (location, picture position, title, timestamp)
- If NEW: Inserts a new entry with quantity = 1

NOTE: This is a single-entry system per barcode (no quantity increment).

================================================================================
2. ITEM PREP FLOW - Committing prep items
================================================================================
Location: app.py lines 4290-4370
Trigger: POST /api/items_prep/commit

When user commits items from the prep system to inventory:

BEHAVIOR:
A) For PICTURE POSITION items:
   - ALWAYS inserts a new entry (never updates existing)
   - Each picture item is treated as unique
   - Creates separate rows even if same barcode

B) For SHELF CODE items:
   - Checks if same barcode + same location already exists
   - If EXISTS at same location: Increments quantity by 1, updates timestamp
   - If NEW or different location: Inserts new entry with quantity = 1

IMPORTANT: This is the PRIMARY flow that can increment quantities!

================================================================================
3. MANUAL "ADD TO SHELF" FLOW (DBmanager.addToSearchRack)
================================================================================
Location: DBmanager.py lines 150-185
Trigger: Called from various submission endpoints

*** EXCLUDED FROM THIS ANALYSIS PER USER REQUEST ***

This is the traditional manual flow where users scan items and add them to
shelves directly. Similar logic to item prep commit flow.

================================================================================
4. ENRICH SEARCHRACK - Data Enrichment
================================================================================
Location: DBmanager.py line 602+ (enrich_searchrack_db function)
Trigger: 
- After eBay/Amazon sync operations
- Manual refresh from /searchrack page
- Auto-sync worker

BEHAVIOR:
- Does NOT insert new rows
- Only UPDATES existing SEARCHRACK entries
- Enriches TITLE, ITEMID, QUANTITY, IMAGE fields
- Looks up data from ebayStore.db, amazonStore.db, and bol.db

IMPORTANT: This is enrichment only, NOT insertion!

================================================================================
5. SOLD ORDER INVENTORY REDUCTION
================================================================================
Location: Not direct insertion, but affects quantities
Trigger: When items are sold

BEHAVIOR:
- Does NOT insert new entries
- DECREMENTS quantity of existing entries
- May DELETE entries when quantity reaches 0

================================================================================
SUMMARY: When NEW entries are added (excluding manual add to shelf)
================================================================================

NEW ENTRIES ARE ADDED IN THESE CASES:

1. Item Prep "done" flow (if barcode doesn't exist yet)
   - Single entry, quantity = 1

2. Item Prep "commit" flow:
   - Picture position items: ALWAYS new entry
   - Shelf code items: New entry if barcode+location combo doesn't exist

QUANTITIES ARE INCREMENTED IN THESE CASES:

1. Item Prep "commit" flow ONLY:
   - Shelf code items with same barcode + same location
   - Increments by 1 per commit

DUPLICATES CAN OCCUR WHEN:

1. Same barcode committed to DIFFERENT shelf locations
   - Each location gets its own entry

2. Same barcode with PICTURE POSITION
   - Each picture item is always unique

3. Multiple commits of same item to SAME location
   - Quantity increments, but only ONE entry exists

================================================================================
EDGE CASES & NOTES
================================================================================

1. The item prep flows check for existing entries by:
   - BARCODE (case-insensitive)
   - ITEM_POSITION (for shelf items)
   - PICTUREPOSITION (must be empty for shelf items to match)

2. Enrichment runs frequently but never creates duplicates
   - Only updates existing entries

3. Returns restocking can add items back via the item prep commit flow

4. The system is designed to prevent accidental duplicates EXCEPT:
   - Picture position items (intentionally unique)
   - Same item in different locations (intentional separate tracking)

================================================================================
