# Custom Item Prep Flow Fix

## Issue
Custom items created via `/item-prep-create-item` were not displaying their image, title, and barcode when transitioning to the `/item-prep` good/bad flow.

## Root Cause
The `/api/items-prep/temp-item` endpoint was using the **raw UPC** (from user input) to:
- Save the item to database
- Generate the image filename  
- Create the cache key for deletion

However, the `/api/bol_lookup` endpoint uses `_normalize_upc()` on all incoming UPCs before querying.

This mismatch meant:
1. Item was saved with raw UPC (e.g., "777000000013")
2. Cache key was deleted with raw UPC: `view//api/bol_lookup?upc=777000000013`
3. When item-prep called `/api/bol_lookup`, it normalized the UPC
4. The **cached response** (with old data or empty result) was served because the cache key didn't match
5. User saw blank image/title/barcode

## Solution
Modified `/api/items-prep/temp-item` to:
1. Normalize the UPC using `_normalize_upc()` before any operations
2. Use normalized UPC for database storage
3. Use normalized UPC for image filename
4. Use normalized UPC for cache key deletion

This ensures cache invalidation works correctly and the custom item is immediately available when looked up in item-prep.

## Files Changed
- `app.py` (lines 2790-2857): Added UPC normalization to save_temp_item()

## Testing
1. Create a custom item via `/item-prep-create-item`
2. Take photo, enter title, enter/generate barcode
3. Click "Complete and Continue"  
4. Verify image, title, and UPC display correctly in `/item-prep`
5. Verify "Good" and "Bad" buttons work properly
