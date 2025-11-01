# rawbol.db Cleanup and Improvements

## Summary
The rawbol.db database has been completely recreated with improvements to handle BOL uploads correctly.

## Changes Made

### 1. Database Recreated
- **Old database deleted** and recreated with clean slate
- All items, upload logs, sync history, and synced lots have been cleared
- Ready for fresh BOL uploads

### 2. Image URL Extraction Fixed
**Problem**: Image URLs were stored as Excel HYPERLINK formulas like:
```
=HYPERLINK("http://slimages.macys.com/is/image/MCY/24513711 ")
```

**Solution**: Updated `rawbol_manager.py` to extract the actual URL from HYPERLINK formulas:
- Detects `=HYPERLINK("url")` pattern
- Extracts the URL using regex
- Stores clean URL in database

### 3. BOL # Column
- **Already exists** in the database schema as `bol_number`
- Now properly extracted from Excel files with the column name "BOL #"
- If BOL # is not present or is empty, it stores an empty string

### 4. Quantity Handling
- **Already correctly implemented** - extracts from these columns (in priority order):
  1. `QUANTITY`
  2. `QTY`
  3. `ORIGINAL QTY`
- Defaults to 1 if no quantity column is found
- Stores as INTEGER in database

## Database Schema

### raw_bol_items table:
- `id` - Primary key
- `upc` - Item UPC barcode
- `item_description` - Item description
- `client_cost` - Unit cost
- `total_client_cost` - Total cost for quantity
- `image_url` - Product image URL (now properly extracted)
- `quantity` - Quantity of items (properly extracted from ORIGINAL QTY, QUANTITY, or QTY)
- `lot_number` - Lot number for tracking
- `bol_number` - BOL # from the Excel file
- `import_date` - Date of import
- `created_at` - Timestamp when added to database

### Other tables:
- `upload_logs` - Tracks each file upload
- `sync_history` - Records sync operations to bol.db
- `synced_lots` - Tracks which lots have been synced to prevent duplicates

## Next Steps

1. **Re-upload your BOL files** through the extractor interface
2. Items will now have:
   - ✅ Clean image URLs (no Excel formulas)
   - ✅ Correct quantities from ORIGINAL QTY column
   - ✅ BOL # properly stored
3. Search in "Macy BOL" section will now find items from rawbol.db (not bol.db)

## Files Modified

1. `rawbol_manager.py` - Added image URL extraction from HYPERLINK formulas
2. `app.py` - Changed "Macy BOL" search mapping from bol.db to rawbol.db
3. `recreate_rawbol.py` - New script to clean and recreate the database

## Testing

After re-uploading your BOL files, you can verify:
```python
# Check if image URLs are clean (no =HYPERLINK)
python -c "import sqlite3; conn = sqlite3.connect('rawbol.db'); cur = conn.cursor(); cur.execute('SELECT image_url FROM raw_bol_items LIMIT 5'); print([r[0] for r in cur.fetchall()]); conn.close()"

# Check if quantities are correct
python -c "import sqlite3; conn = sqlite3.connect('rawbol.db'); cur = conn.cursor(); cur.execute('SELECT upc, quantity FROM raw_bol_items LIMIT 5'); print(cur.fetchall()); conn.close()"

# Check if BOL # is stored
python -c "import sqlite3; conn = sqlite3.connect('rawbol.db'); cur = conn.cursor(); cur.execute('SELECT upc, bol_number FROM raw_bol_items LIMIT 5'); print(cur.fetchall()); conn.close()"
```
