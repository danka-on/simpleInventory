# Code Migration Changes - rack.db to searchRack.db

## Migration Completed: November 1, 2024

This document tracks all code changes made to remove rack.db dependencies after consolidating to single searchRack.db database.

## Files Modified

### DBmanager.py

#### Functions Deleted:
1. **addToRack()** (previously at line 61)
   - Wrote to rack.db INVENTORY table
   - Replaced by: addToSearchRack() which writes directly to searchRack.db

2. **updateSearchRackDB()** (previously at line 622)
   - Deleted and rebuilt searchRack from rack.db
   - No longer needed since searchRack.db is now the primary database

#### Functions Modified:
1. **createSearchRackDB()** (line 512)
   - BEFORE: Connected to rack.db and bol.db, populated searchRack from rack data
   - AFTER: Only creates table structure, no data population from rack.db
   - Simplified to just ensure SEARCHRACK table and CREATED_AT column exist

2. **store_ebay_order()** (line 461)
   - BEFORE: Looked up location from rack.db INVENTORY table
   - AFTER: Looks up location from searchRack.db SEARCHRACK table
   - Changed: `rack.db` → `searchRack.db` and `INVENTORY` → `SEARCHRACK`

### app.py

#### Import Statement Updated:
- BEFORE: `from DBmanager import ebayStoreDB, amazonStoreDB, addToRack, store_ebay_order, createSearchRackDB, updateSearchRackDB, addToSearchRack`
- AFTER: `from DBmanager import ebayStoreDB, amazonStoreDB, store_ebay_order, createSearchRackDB, addToSearchRack`
- Removed: `addToRack`, `updateSearchRackDB`

#### Function Calls Removed:

1. **Barcode Scanning Flow** (line ~2233)
   - BEFORE: Called both `addToRack()` and `addToSearchRack()`
   - AFTER: Only calls `addToSearchRack()`
   - Effect: Single write to searchRack.db instead of duplicate writes

2. **refresh_searchrack()** (line ~5010)
   - BEFORE: Called `createSearchRackDB()`, then `updateSearchRackDB()`, then `enrich_searchrack_db()`
   - AFTER: Calls `createSearchRackDB()`, then `enrich_searchrack_db()`
   - Removed: `updateSearchRackDB()` call

3. **Sync Operations** (lines ~5136, ~5203)
   - BEFORE: Called `updateSearchRackDB()` before enrichment
   - AFTER: Only calls `enrich_searchrack_db()`
   - Affected endpoints:
     - eBay listings sync in main sync_all
     - `/api/sync/ebay-listings`

#### Database Mappings Updated:

Removed `'rack': 'rack.db'` from 4 locations:
1. `/api/search/<db_key>` (line ~3796)
2. `/api/update/<db_key>/<int:item_id>` (line ~4333)
3. `/api/delete/<db_key>/<int:item_id>` (line ~4691)
4. `/api/restore_deleted/<int:del_id>` (line ~4760)

#### Database Connections Updated:

1. **api_lookup_location()** (line ~4096)
   - BEFORE: `sqlite3.connect('rack.db')` with `INVENTORY` table
   - AFTER: `sqlite3.connect('searchRack.db')` with `SEARCHRACK` table

2. **Shelf Rename Cascade** (line ~4443)
   - BEFORE: Updated both searchRack.db and rack.db
   - AFTER: Only updates searchRack.db
   - Removed: rack.db update code and logging reference

### rebuild_searchrack.py

#### Complete Rewrite:
- BEFORE: Rebuilt searchRack from rack.db using `updateSearchRackDB()`
- AFTER: Only refreshes enrichment data from store databases
- Updated comments to reflect new single-database architecture
- Removed: `updateSearchRackDB` import and call
- Purpose: Changed from "rebuild from rack.db" to "refresh enrichment"

## Database Architecture Changes

### Before Migration:
- **rack.db**: Primary storage for barcode scans (INVENTORY table)
- **searchRack.db**: Search database synced from rack.db via updateSearchRackDB()
- **Sync mechanism**: Periodic DELETE and rebuild of entire searchRack

### After Migration:
- **searchRack.db**: Single primary database (SEARCHRACK table)
- **rack.db**: Archived to `rack.db.archived-20251101_120902`
- **Backup mechanism**: Rotating 10-day backups via `rotating_backup.py`
- **No sync needed**: Direct writes to searchRack.db

## Data Flow Changes

### Barcode Scanning:
- BEFORE: `addToRack()` → rack.db, then `addToSearchRack()` → searchRack.db (duplicate writes)
- AFTER: `addToSearchRack()` → searchRack.db (single write with enrichment)

### Search Operations:
- BEFORE: Read from searchRack.db (which was synced from rack.db)
- AFTER: Read directly from searchRack.db (primary source)

### Location Lookups:
- BEFORE: Checked rack.db INVENTORY table for barcode locations
- AFTER: Checks searchRack.db SEARCHRACK table

### Enrichment:
- BEFORE: `updateSearchRackDB()` deleted/rebuilt, then `enrich_searchrack_db()` enriched
- AFTER: `enrich_searchrack_db()` enriches existing records in place

## Testing Checklist

- [x] No Python syntax errors in modified files
- [ ] Barcode scanning works and writes to searchRack.db
- [ ] Search functionality returns results
- [ ] Location lookups work for existing items
- [ ] Shelf rename cascades to searchRack.db items
- [ ] Enrichment process updates titles/images
- [ ] No attempts to write to archived rack.db
- [ ] Daily backups continue to work

## Files Not Modified

These files still reference rack.db but are unused or for historical reference:
- `createRack()` function in DBmanager.py (not called anywhere)
- Various check_*.py scripts (diagnostic tools)
- Migration scripts in migration/archive directories

## Rollback Procedure

If issues arise, restore from pre-migration backups:
1. Stop the application
2. Restore `rack.db.pre-migration-20251101_120902` → `rack.db`
3. Restore `searchRack.db.pre-migration-20251101_120902` → `searchRack.db`
4. Revert code changes using git: `git checkout <commit_before_migration>`
5. Restart application

## Next Steps

1. Test barcode scanning workflow thoroughly
2. Verify search functionality with various queries
3. Monitor application logs for any rack.db connection attempts
4. Run daily backup manually to verify automation works
5. After 1 week of stable operation, can safely delete rack.db.archived file
