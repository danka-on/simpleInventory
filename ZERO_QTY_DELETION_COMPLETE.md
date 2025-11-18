# Zero-Quantity Deletion System - Complete Implementation

## Overview
The zero-quantity deletion system automatically marks items with quantity=0 for deletion after a configurable grace period (default: 48 hours). This prevents clutter from items that have been fully sold or removed.

## What Was Done

### 1. UI Addition to Multi-DB Search (searchrack.html)
- **Added pending deletions notice** similar to sold orders pending removals
- Shows when viewing Inventory tab
- Displays items pending deletion with:
  - Barcode, title, position
  - Status (hours remaining, ready to delete, or cancelled)
  - Action buttons (Cancel, Re-enable, Delete now)
- Auto-hides when viewing other database tabs

### 2. API Endpoints Added/Updated

#### Updated: `/api/zero_qty_pending` (GET)
- Returns list of items pending deletion
- Calculates time remaining until deletion
- Includes grace period setting
- Adds `deletion_cancelled` column if not exists
- Returns enriched data with position, barcode, etc.

#### Added: `/api/zero_qty_cancel/<barcode>` (POST)
- Cancel automatic deletion for a zero-quantity item
- Sets `deletion_cancelled = 1`
- Item remains in inventory but won't be auto-deleted

#### Added: `/api/zero_qty_allow/<barcode>` (POST)
- Re-enable automatic deletion for a previously cancelled item
- Sets `deletion_cancelled = 0`

#### Updated: `/api/zero_qty_delete_now/<barcode>` (POST)
- Changed from accepting ID to accepting barcode
- Immediately deletes item (bypasses grace period)
- Archives to `archived_searchrack` table
- Removes from pending queue

### 3. Database Changes
- **Added column**: `deletion_cancelled INTEGER DEFAULT 0` to `zero_qty_pending_deletion` table
- **Fixed setting**: Changed `interval_minutes` from 1 minute to 2880 minutes (48 hours)

### 4. Helper Scripts Created

#### `mark_existing_zero_qty.py`
- One-time script to mark existing zero-quantity items for deletion
- Found and marked 1 item (ID=779, barcode: 021241169547)
- Sets default grace period if not configured

#### `update_pending_times.py`
- Updates existing pending deletions to use new grace period
- Useful after changing the grace period setting

## How It Works

### Automatic Marking
When an item's quantity is updated to 0 (via `/api/update` endpoint):
1. System checks if quantity changed to 0
2. Creates entry in `zero_qty_pending_deletion` table
3. Sets `delete_at` to current time + grace period
4. Item shows in "Pending Automatic Inventory Removals" section

### Grace Period Management
- **Default**: 48 hours (2880 minutes)
- **Configurable**: via `zero_qty_settings` table (`interval_minutes` key)
- **Display**: Shows hours remaining until deletion

### User Actions
1. **Cancel deletion**: Marks item as cancelled, won't be auto-deleted
2. **Re-enable deletion**: Uncancels item, resumes deletion countdown
3. **Delete now**: Immediately archives and removes item (bypasses grace period)

### Automatic Cleanup
- Items with quantity increased back above 0 are automatically removed from pending queue
- When grace period expires, item becomes eligible for deletion (would need a background job to execute)

## UI Location
- **Primary**: Multi-DB Search → Inventory tab → "🗑️ Pending Zero-Quantity Deletions" notice
- **Settings**: Still accessible via /misc page for grace period configuration

## Testing
1. Start the app: `python app.py`
2. Navigate to Multi-DB Search, select Inventory tab
3. Should see pending deletion notice with item 779
4. Test cancel, re-enable, and delete now buttons

## Configuration
To change grace period:
```python
import sqlite3
conn = sqlite3.connect('searchRack.db')
cur = conn.cursor()
# Set to desired minutes (e.g., 2880 = 48 hours)
cur.execute("UPDATE zero_qty_settings SET value = '2880' WHERE key = 'interval_minutes'")
conn.commit()
conn.close()
```

## Notes
- Items are archived to `archived_searchrack` table before deletion
- The system marks items when quantity CHANGES to 0, not for existing zero-qty items
- Use `mark_existing_zero_qty.py` to catch up existing items
- Barcode padding (12 digits) is properly handled in all endpoints
