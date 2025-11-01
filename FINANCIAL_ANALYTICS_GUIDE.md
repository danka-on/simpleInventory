# 💰 Financial Analytics Dashboard - User Guide

## Overview
A comprehensive financial analytics dashboard that tracks sales, costs, fees, and profits across your eBay and Amazon stores.

## Features

### 📊 Summary Cards
- **Total Revenue**: All sales revenue from selected period
- **Total Costs**: Purchase costs from BOL data
- **Total Fees**: Combined seller fees and taxes
- **Net Profit**: Revenue - Costs - Fees
- **Profit Margin**: Net profit as percentage of revenue

### 📈 Interactive Charts
1. **Revenue vs Costs Over Time**: Line chart showing daily trends
2. **Sales by Store**: Pie chart comparing eBay vs Amazon revenue

### 🔍 Filters Available
- **Date Range**: 7/30/90/180/365 days, All Time, or Custom Range
- **Store**: All Stores, eBay only, or Amazon only
- **Cost Data**: All Items, With Cost Data, or Missing Cost Data

### 📋 Data Table
Sortable columns:
- Date sold
- Store (eBay/Amazon badge)
- Item title
- UPC/Barcode
- Quantity
- Sale Price
- Cost (from BOL)
- Fees (seller + taxes)
- Net Profit (color-coded: green = profit, red = loss)
- Profit Margin %

**Features**:
- Search by title or UPC
- Click column headers to sort
- Pagination (50 items per page)
- Color-coded profit indicators

## Data Sources

### Primary Data: `sold.db` (orders table)
```sql
- order_id: Order identifier
- item_id: Product UPC/barcode
- title: Product name
- quantity: Items sold
- price: Sale price per item
- seller_fee: Platform seller fees
- taxes: Tax charges
- paid_time: Date sold
- store: 'ebay' or 'amazon'
- barcode: Product UPC
```

### Cost Data: `bol.db` (bol_items table)
```sql
- upc: Product barcode (matched to sold items)
- client_cost: Purchase cost per item
- item_description: Product title
- lot_number: BOL shipment lot
- bol_number: Bill of lading number
```

## Calculations

### Per Transaction:
```
Revenue = price × quantity
Cost = client_cost × quantity (from BOL)
Fees = seller_fee + taxes
Gross Profit = Revenue - Cost
Net Profit = Revenue - Cost - Fees
Profit Margin % = (Net Profit / Revenue) × 100
```

### Aggregated Metrics:
- Sum all transactions in selected period
- Average cost per item
- Total items sold count

## Missing Cost Data
Items without BOL cost data show as:
- "No data" in Cost column
- Included in profit calculations as $0 cost
- Can be filtered separately using "Missing Cost Data" filter

## Default View
- **Date Range**: Last 30 Days
- **Store**: All Stores
- **Cost Filter**: All Items
- **Sort**: Most recent first

## Usage Tips
1. **Track Monthly Performance**: Use "Last 30 Days" to see current month trends
2. **Identify Profitable Items**: Sort by "Margin %" to find best sellers
3. **Find Missing Costs**: Filter by "Missing Cost Data" to see items needing BOL entry
4. **Compare Stores**: Use store filter to analyze eBay vs Amazon separately
5. **Search Specific Items**: Use search box to find transactions by title or UPC

## Future Enhancements (Planned)
- 📦 Shipping cost tracking (via API)
- ↩️ Returns tracking (new returns.db)
- 📤 Export to CSV
- 📊 More chart types (top items, category breakdown)
- 📅 Month-over-month comparisons
- 🎯 Profit targets and goals

## Technical Notes
- Data refreshes on demand (click "Refresh Data" button)
- Charts use Chart.js library
- All calculations done client-side after data fetch
- Responsive design works on mobile and desktop

## Access
Navigate to: **Tools → 💰 Financial Analytics**

Or directly: `http://localhost:5000/financial-analytics`
