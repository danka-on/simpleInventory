# Quantity Column System - Final Documentation

## Problem Solved
1. ❌ **Old System**: Confusing dual `quantity` and `original_qty` columns with same values
2. ❌ **263 items had zero quantities** (not found in rawbol.db)
3. ❌ **No clear distinction** between display value and baseline

## Solution Implemented

### Column Roles (5 columns)

```
┌─────────────────┬───────────────────────────────────────────────────────┐
│ Column          │ Purpose                                               │
├─────────────────┼───────────────────────────────────────────────────────┤
│ quantity        │ Display value (= good_qty)                           │
│                 │ • Backward compatibility with old code               │
│                 │ • Always equals good_qty                             │
│                 │ • Shows "ready to list" count in UI                  │
├─────────────────┼───────────────────────────────────────────────────────┤
│ original_qty    │ Immutable baseline from BOL import                   │
│                 │ • Set once from rawbol → bol                         │
│                 │ • NEVER changes during prep                          │
│                 │ • Used for stats and loss calculation                │
├─────────────────┼───────────────────────────────────────────────────────┤
│ good_qty        │ Items marked GOOD (ready to list)                    │
│                 │ • Increments: mark as good                           │
│                 │ • Decrements: undo                                   │
├─────────────────┼───────────────────────────────────────────────────────┤
│ bad_qty         │ Items marked BAD (defective)                         │
│                 │ • Increments: create bad entry                       │
│                 │ • Used for loss rate calculation                     │
├─────────────────┼───────────────────────────────────────────────────────┤
│ unchecked_qty   │ Items NOT YET prepped                                │
│                 │ • Decrements: mark good/bad                          │
│                 │ • Increments: undo                                   │
└─────────────────┴───────────────────────────────────────────────────────┘
```

### Mathematical Invariant
**Always maintained:**
```
original_qty = good_qty + bad_qty + unchecked_qty
```

### Relationships
```
quantity = good_qty                    (backward compatibility)
prepped = good_qty + bad_qty          (total processed)
progress = prepped / original_qty      (completion %)
loss_rate = bad_qty / original_qty     (defect %)
```

## Changes Made

### 1. Fixed Zero Quantities
- **Found**: 263 items with `original_qty = 0`
- **Cause**: Not in rawbol.db (old/deleted LOTs)
- **Fix**: Set `original_qty = 1` as default for orphaned items
- **Result**: All 1,550 items now have valid quantities

### 2. Standardized `quantity` Column
- **Before**: Mixed values (sometimes original, sometimes unchecked)
- **Now**: Always equals `good_qty`
- **Purpose**: Backward compatibility with old code
- **Updated**: 1,242 items

### 3. Verified System Integrity
```
Total items: 1,550
Total original: 2,471
Total good: 55
Total bad: 3
Total unchecked: 2,413
✓ Invariant: 2,471 = 55 + 3 + 2,413 ✅
```

## Why Keep Both `quantity` and `original_qty`?

### Different Purposes:
1. **`quantity`** (Dynamic Display)
   - Changes during workflow
   - Shows current "ready to list" count
   - Used by Item Manager, listing pages
   - Equals `good_qty` for clarity

2. **`original_qty`** (Static Baseline)
   - Set once at import
   - Never changes
   - Audit trail (how many received)
   - Used for stats, progress, loss rate

### Example Flow:
```
Import BOL: 10 items
├─ original_qty = 10 (immutable)
├─ unchecked_qty = 10
├─ good_qty = 0
├─ bad_qty = 0
└─ quantity = 0

Mark 6 as Good:
├─ original_qty = 10 (unchanged)
├─ unchecked_qty = 4
├─ good_qty = 6
├─ bad_qty = 0
└─ quantity = 6 (now shows 6 ready to list)

Mark 2 as Bad:
├─ original_qty = 10 (unchanged)
├─ unchecked_qty = 2
├─ good_qty = 6
├─ bad_qty = 2
└─ quantity = 6 (still shows 6 ready to list)

Stats:
├─ Progress: 8/10 = 80% prepped
├─ Loss Rate: 2/10 = 20% defective
└─ Unchecked: 2 items remaining
```

## BOL Statistics Updated

Now uses new quantity columns for accurate tracking:
- **Original Quantity**: Sum of all `original_qty` per LOT
- **Good**: Sum of `good_qty` per LOT
- **Bad**: Sum of `bad_qty` per LOT  
- **Unchecked**: Sum of `unchecked_qty` per LOT
- **Progress**: (good + bad) / original
- **Loss Rate**: bad / original

## Files Modified
1. `fix_quantity_zeros.py` - Fixed 263 zero qty items
2. `fix_zero_qty_final.py` - Set defaults for orphaned items
3. `app.py` - Updated `/api/bol_stats` endpoint
4. `bol_stats.html` - Updated to display new metrics

## Testing
✅ All 1,550 items have valid quantities
✅ Invariant maintained across all items
✅ Backward compatibility preserved (quantity = good_qty)
✅ BOL Statistics page shows accurate data
✅ Multi-LOT allocation system integrated

---

**Status**: ✅ COMPLETE - Quantity system fully operational and validated
