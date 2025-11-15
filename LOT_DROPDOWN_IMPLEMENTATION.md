# LOT Dropdown Implementation

## Overview
Replaced the LOT allocation modal with a persistent LOT dropdown selector in the item-prep page header. The selected LOT is stored in the Flask session and persists across all pages in the workflow.

## Changes Made

### 1. Backend (app.py)

#### Session Support
- Added `session` import from Flask
- Added `app.secret_key` for session management (required for Flask sessions)

#### New API Endpoints

**GET /api/lots**
- Returns list of available LOTs from rawbol.db (sorted newest first)
- Includes currently selected LOT from session
- Auto-selects newest LOT if none selected
```json
{
  "lots": [
    {"lot_number": "LOT-123", "import_date": "2025-11-15"},
    {"lot_number": "LOT-122", "import_date": "2025-11-14"}
  ],
  "selected_lot": "LOT-123"
}
```

**POST /api/lots/select**
- Saves selected LOT to Flask session
- Persists across all pages
```json
{
  "lot_number": "LOT-123"
}
```

#### Updated Status Endpoint (POST /api/items_prep/status)
- Removed multi-LOT allocation modal logic
- Now uses `session.get('selected_lot')` to determine which LOT to use
- For GOOD status:
  1. Gets selected LOT from session (returns error if none selected)
  2. Finds or creates bol_items entry for UPC in selected LOT
  3. If UPC doesn't exist in selected LOT, pulls from rawbol.db
  4. Increments good_qty and decrements unchecked_qty for that LOT
  5. Updates only the specific LOT entry (not all LOTs for that UPC)

### 2. Frontend (item_prep.html)

#### UI Changes

**LOT Selector Card** (added after header)
```html
<div class="card">
  <label>Working LOT:</label>
  <select id="lotSelector">...</select>
  <div id="lotDate">Imported: 2025-11-15</div>
</div>
```

**Removed LOT Allocation Modal**
- Deleted modal HTML
- Removed `showLotAllocationModal()` function
- Removed `autoAdjustAllocations()` function

#### JavaScript Changes

**LOT Management**
```javascript
let selectedLot = null;
let availableLots = [];

async function loadLots() {
  // Fetches /api/lots
  // Populates dropdown with LOTs
  // Shows import date for selected LOT
  // Auto-saves selection changes to session
}
```

**Initialization**
- Added `await loadLots()` to first DOMContentLoaded event
- Ensures LOT selector is populated before any item prep operations

**Status Marking**
- Simplified `setStatus()` function - no longer handles LOT allocation
- Backend now uses session-stored LOT automatically

## User Flow

1. **Page Load**: LOT dropdown loads and defaults to newest LOT
2. **Select LOT**: User can change LOT from dropdown (saved to session)
3. **Scan Item**: User scans/enters barcode as before
4. **Mark GOOD**: Item is added to currently selected LOT
5. **Persistence**: Selected LOT persists across page navigation (diagnostic, print queue, etc.)

## Benefits

1. **Simpler UX**: No modal popups interrupting workflow
2. **Explicit Control**: User always knows which LOT they're working with
3. **Persistent Selection**: LOT stays selected across all pages
4. **Scalable**: Works with any number of LOTs without complex allocation logic
5. **Faster**: One less API call per item (no allocation step)

## Migration Notes

- Old LOT allocation logic still exists in `/api/items_prep/allocate_lots` endpoint (unused)
- Can be safely removed or kept for backward compatibility
- No database changes required
- Session data is temporary (clears on browser close or server restart)

## Testing Checklist

- [ ] LOT dropdown loads with available LOTs
- [ ] Newest LOT is selected by default
- [ ] Changing LOT saves to session
- [ ] Marking item as GOOD adds to selected LOT
- [ ] Selected LOT persists when navigating to diagnostic page and back
- [ ] Selected LOT persists when navigating to print queue and back
- [ ] Error shown if no LOT selected when marking item
- [ ] Error shown if UPC doesn't exist in selected LOT (creates from rawbol if available)

## Future Enhancements

1. **LOT Statistics**: Show unchecked count for selected LOT
2. **LOT Filtering**: Filter/search LOTs by date or name
3. **Multi-User**: Store LOT per user session (for multiple prep stations)
4. **LOT Creation**: Add new LOT directly from prep page
