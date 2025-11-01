"""
ROOT CAUSE ANALYSIS: 21 items added to searchRack.db on 2025-10-31T14:11:59
================================================================================

WHAT HAPPENED:
--------------
21 items were added to searchRack.db inventory section on 2025-10-31 at 14:11:59
with the exact same timestamp (down to microseconds: 2025-10-31T14:11:59.081542).

All items had:
- No TITLE (NULL/empty)
- Various barcodes: 4291206417082, 086279084392 (multiple copies), empty strings
- Exact same CREATED_AT timestamp

WHY IT HAPPENED:
----------------
The updateSearchRackDB() function was called, which:

1. DELETES all existing items from SEARCHRACK table
2. Rebuilds it from rack.db INVENTORY table
3. Tries to enrich with titles from bol.db by looking up UPC/barcode
4. Assigns the same timestamp to all items in the batch

From DBmanager.py line 622-680:
```python
def updateSearchRackDB():
    # ... setup ...
    
    # Delete all existing items
    search_cur.execute('DELETE FROM SEARCHRACK')
    search_conn.commit()
    
    # Rebuild from rack.db
    rack_cur.execute('SELECT BARCODE, ITEM_POSITION, IMAGES, PICTUREPOSITION FROM INVENTORY')
    rack_items = rack_cur.fetchall()
    now_iso = datetime.datetime.utcnow().isoformat()  # Single timestamp for entire batch
    
    for barcode, item_position, images, pictureposition in rack_items:
        # Try to get title from bol.db
        bol_cur.execute('SELECT item_description FROM bol_items WHERE upc=? COLLATE NOCASE', (barcode,))
        bol_row = bol_cur.fetchone()
        title = bol_row[0] if bol_row else None  # Title is NULL if not found in bol.db
        
        # Insert with same timestamp for all items
        search_cur.execute('INSERT INTO SEARCHRACK ... VALUES (..., created_val)')
```

WHERE IT'S CALLED:
------------------
updateSearchRackDB() is called in 3 places:

1. /refresh_searchrack endpoint (app.py line 5015)
   - Manual refresh from UI

2. /api/sync/ebay-listings endpoint (app.py line 5141)
   - Part of eBay listings sync

3. /api/sync/all endpoint (app.py line 5209)  
   - Part of "sync everything" operation

THE PROBLEM:
------------
1. The 21 items existed in rack.db with barcodes
2. Those barcodes did NOT exist in bol.db (or BOL data was cleared earlier)
3. When updateSearchRackDB() ran, it rebuilt SEARCHRACK but couldn't find titles
4. Result: 21 items with barcodes but no titles, all with same timestamp

This happened because:
- rawbol.db was recreated (cleaned) earlier, removing all BOL data
- The old rawbol data used to sync to bol.db, providing titles
- After cleanup, bol.db had no matching records for these barcodes
- updateSearchRackDB() created orphaned records with no enrichment data

SOLUTION IMPLEMENTED:
---------------------
✅ The 21 items have been deleted from searchRack.db

PREVENTION:
-----------
To prevent this in the future:

1. When recreating rawbol.db, also check if bol.db should be cleared
2. After clearing BOL data, run updateSearchRackDB() to clean orphaned records
3. Consider adding a filter in updateSearchRackDB() to skip items with:
   - Empty barcodes
   - No title AND no other enrichment data

RECOMMENDATIONS:
----------------
1. After re-uploading BOL files to rawbol.db, sync them to bol.db
2. Then run updateSearchRackDB() to properly enrich the inventory
3. Consider modifying updateSearchRackDB() to:
   - Skip items with empty/null barcodes
   - Try ebayStore.db for enrichment if bol.db has no title
   - Log warnings when items can't be enriched
