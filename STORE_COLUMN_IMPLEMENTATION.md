# Store Column Implementation Summary

## Changes Made

### 1. Database Schema (sold.db)
- Added `store` column to `orders` table
  - Type: TEXT
  - Values: 'ebay' or 'amazon'
  - Default: 'ebay' for existing records

### 2. Backend Updates

#### DBmanager.py
- Updated `store_ebay_order()` function:
  - Added 'store' column to CREATE TABLE statement
  - Added migration logic to add column if missing
  - Set store='ebay' for all eBay orders

#### amazon_manager.py
- Updated `sync_orders_to_db()` function:
  - Added 'store' column to CREATE TABLE statement
  - Added migration logic to add column if missing
  - Set store='amazon' for all Amazon orders

#### app.py
- Updated `/api/amazon/orders` endpoint:
  - Changed filter from `source='amazon'` to `store='amazon'`

### 3. Frontend Updates

#### templates/searchrack.html
- Added "Store" column header (only visible when viewing sold items)
- Added store badge cell displaying:
  - 🟠 Amazon (orange badge) for Amazon orders
  - 🔵 eBay (blue badge) for eBay orders
- Badges are styled with platform colors:
  - Amazon: #FF9900 (orange)
  - eBay: #0064D2 (blue)

### 4. Migration Script
Created `migrate_store_column.py`:
- Adds 'store' column if missing
- Sets all existing orders to 'ebay'
- Shows distribution of orders by store

## Current Database Status

Total orders: 66
- eBay: 48 orders
- Amazon: 18 orders

## How to Use

### Viewing Orders in SearchRack
1. Go to `/searchrack`
2. Select "Sold" database
3. The Store column will show colored badges:
   - 🟠 Amazon for Amazon orders
   - 🔵 eBay for eBay orders

### Syncing Orders
- **eBay**: Use "Get eBay Sold Orders" button in Tools → automatically sets store='ebay'
- **Amazon**: Use "Sync Amazon Orders" button in Tools → automatically sets store='amazon'

### Automatic Inventory Reduction
- Works seamlessly across both stores
- Grace period applies to both eBay and Amazon orders
- Pending removals UI shows all orders regardless of store
- Can filter/identify by store badge when reviewing

## Testing
All existing sold order functionality continues to work:
- Grace period (48h default, configurable)
- Pending removals
- Cancel/Re-enable removal
- Remove now
- Logging to removed.db
- Undo capability
