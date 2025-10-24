# Automatic Inventory Reduction - 48-Hour Grace Period System

## Overview
When items are sold on eBay, the system now automatically reduces inventory in `searchRack.db` after a 48-hour grace period from the shipping date. This prevents premature inventory reduction and gives users time to cancel if needed.

## How It Works

### 1. Sold Order Processing
When sold orders are fetched from eBay (via `/get-sold-orders`):
- Orders are stored in `sold.db` with all details including `shipped_time`
- The system automatically enriches orders with barcodes from `ebayStore.db`
- Each order gets a `rackupdated` flag (default: 0) and `removal_cancelled` flag (default: 0)

### 2. Eligibility Requirements
For an order to be eligible for automatic inventory reduction, it must meet ALL conditions:
- ✅ Has a barcode/UPC
- ✅ Has been shipped (`shipped_time` is not null)
- ✅ 48 hours have passed since `shipped_time`
- ✅ `rackupdated = 0` (not already processed)
- ✅ `removal_cancelled = 0` (user hasn't cancelled)

### 3. Grace Period Calculation
```python
hours_since_shipped = (current_time - shipped_time) / 3600
is_eligible = hours_since_shipped >= 48
```

### 4. Automatic Processing
When `/get-sold-orders` is called:
1. Fetches new sold orders from eBay
2. Calls `process_sold_orders_inventory_reduction()`
3. Finds all eligible orders (shipped + past 48h grace period)
4. For each eligible order:
   - Looks up item in `searchRack.db` by barcode (ITEMID column)
   - Reduces quantity: `new_qty = max(0, current_qty - sold_qty)`
   - Marks order as processed: `rackupdated = 1`

## User Interface Features

### Viewing Pending Removals
When viewing the **Sold** section in `/searchrack`:
1. A yellow notice box appears showing all pending removals
2. Each order shows:
   - Order ID, Title, Barcode, Quantity
   - Status: "⏰ Xh remaining" or "⚠️ Ready to remove"
   - Cancel/Re-enable button

### Cancelling Automatic Removal
Users can cancel automatic inventory reduction for specific orders:
1. Go to `/searchrack`
2. Select the **Sold** database filter
3. In the pending removals notice, click **Cancel** button for an order
4. System sets `removal_cancelled = 1` for that order
5. Order will be skipped during automatic processing
6. Users must manually adjust inventory for cancelled orders

### Re-enabling Automatic Removal
If a user cancels by mistake:
1. Click the **Re-enable** button for the cancelled order
2. System sets `removal_cancelled = 0`
3. Order will be processed normally once past 48h grace period

## API Endpoints

### GET `/api/sold/pending-removals`
Returns list of shipped orders pending inventory reduction
```json
{
  "success": true,
  "orders": [
    {
      "id": 123,
      "order_id": "02-13747-72198",
      "barcode": "672275302846",
      "quantity": 1,
      "shipped_time": "2025-10-23T13:39:27.000Z",
      "hours_remaining": 22.0,
      "is_eligible_for_removal": false,
      "removal_cancelled": 0
    }
  ]
}
```

### POST `/api/sold/cancel-removal/<order_id>`
Cancel automatic inventory removal for a specific order
```json
{
  "success": true,
  "message": "Automatic removal cancelled"
}
```

### POST `/api/sold/allow-removal/<order_id>`
Re-enable automatic inventory removal for a cancelled order
```json
{
  "success": true,
  "message": "Automatic removal re-enabled"
}
```

## Database Schema Changes

### `sold.db` - orders table
New columns added:
- `removal_cancelled INTEGER DEFAULT 0` - User cancellation flag
- Existing: `rackupdated INTEGER DEFAULT 0` - Processing status flag
- Existing: `shipped_time TEXT` - Shipping timestamp from eBay

## Testing

Run the test script to verify functionality:
```bash
python test_grace_period.py
```

This will show:
- All shipped orders and their grace period status
- How many hours remaining until eligible for removal
- Which orders have been processed
- Which orders are pending

## Important Notes

1. **Shipping Date Required**: Only orders with `shipped_time` are eligible. Unshipped orders are ignored.

2. **Grace Period Protection**: The 48-hour grace period starts from `shipped_time`, not `paid_time`.

3. **Manual Override**: Users can prevent automatic reduction by cancelling in the UI.

4. **Idempotent Processing**: Once processed (`rackupdated = 1`), an order won't be processed again.

5. **Barcode Matching**: Items are matched in `searchRack.db` using the `ITEMID` column (which stores barcodes).

6. **Quantity Safety**: Quantities never go below 0 (`max(0, current_qty - sold_qty)`).

## Workflow Example

1. **Day 0 - Item Sold & Shipped**
   - Order placed and paid on eBay
   - Seller ships the item
   - eBay records `shipped_time = 2025-10-20 10:00:00 UTC`

2. **Day 1 - Fetch Sold Orders**
   - User clicks "Get Sold Orders" in UI
   - Order is saved to `sold.db` with barcode enrichment
   - System checks grace period: 24h passed (< 48h) → Not eligible yet
   - UI shows: "⏰ 24h remaining"

3. **Day 2 - Still in Grace Period**
   - System checks: 36h passed (< 48h) → Not eligible
   - UI shows: "⏰ 12h remaining"
   - User can still cancel if needed

4. **Day 3 - Auto-Processing**
   - System checks: 50h passed (> 48h) → Eligible!
   - UI shows: "⚠️ Ready to remove"
   - Next time "Get Sold Orders" is run, inventory is automatically reduced
   - Order marked as `rackupdated = 1`

5. **Alternative: User Cancels**
   - At any time before processing, user clicks "Cancel" in UI
   - System sets `removal_cancelled = 1`
   - Order is skipped during automatic processing
   - User must manually adjust inventory

## Future Enhancements

Possible improvements:
- Scheduled automatic processing (cron job)
- Email notifications before inventory reduction
- Audit log of all inventory changes
- Bulk cancel/re-enable operations
- Configurable grace period (currently hardcoded to 48h)
