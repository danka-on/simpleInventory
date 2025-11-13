# BOL Duplicate Consolidation - Completed

## Issue Identified
UPC `48552584265` was showing up in **"11 lots"** in the multi-database search, but investigation revealed:
- Only **4 actual LOTs**: 16264617, 16423315, 16448708, 16578199
- **10 entries in rawbol.db** due to same-LOT duplicates (Excel files had duplicate rows for same UPC)

## Root Cause
**Supplier BOL Excel files contained duplicate rows for the same UPC within the same LOT.**

All duplicate entries had:
- Identical `import_date` and `created_at` timestamps
- Same LOT number
- Same item description
- Different quantities on each row

**This was NOT a code bug** - the Excel files literally had the same UPC on multiple lines.

## Analysis Results
- **78 UPCs** had same-LOT duplicates
- **110 duplicate rows** total
- All duplicates were from single-upload events (same timestamp)

### Examples:
- UPC `400013532749` LOT 16264617: 8 entries → quantities: 1, 4, 4, 27, 4, 4, 12, 27
- UPC `48552584265` LOT 16448708: 4 entries → quantities: 1, 1, 1, 1
- UPC `48552584265` LOT 16578199: 4 entries → quantities: 1, 1, 1, 1

## Solution Implemented

### 1. Consolidated Existing Data
✅ **Script:** `consolidate_rawbol_duplicates.py`
- Merged 78 UPC+LOT combinations
- Deleted 110 duplicate rows
- Summed quantities: 1,509 rows → 1,399 rows
- **Total quantity unchanged**: 2,235

### 2. Updated BOL Import Code
✅ **File:** `rawbol_manager.py` → `insert_raw_bol_items()`
- **New logic:** Consolidates same-UPC duplicates within the same LOT during import
- Sums quantities from duplicate rows
- Keeps best non-empty description/image from all duplicates
- Inserts only ONE row per UPC per LOT

### 3. Verification
✅ **Before:**
```
Unique UPCs: 1,283
Total rows: 1,509
Total quantity: 2,235
Same-LOT duplicates: 78
```

✅ **After:**
```
Unique UPCs: 1,283
Total rows: 1,399
Total quantity: 2,235
Same-LOT duplicates: 0
```

✅ **UPC 48552584265 now shows:**
- LOT 16264617: 1 entry, qty=1
- LOT 16423315: 1 entry, qty=2
- LOT 16448708: 1 entry, qty=4 (was 4 separate entries)
- LOT 16578199: 1 entry, qty=4 (was 4 separate entries)
- **Total: 4 LOTs** (correct!)

## Future Prevention
**All future BOL uploads will automatically:**
1. Detect duplicate UPCs within the same LOT
2. Sum their quantities
3. Store only ONE consolidated entry per UPC per LOT

**Cross-LOT duplicates are still supported** - same UPC can appear in multiple LOTs (intended behavior for repeat purchases).

## Files Modified
- `rawbol_manager.py` - Updated `insert_raw_bol_items()` function
- `consolidate_rawbol_duplicates.py` - One-time cleanup script (already executed)

## Date Completed
November 13, 2025
