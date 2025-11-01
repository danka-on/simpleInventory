# Seller Fee Tracking - Implementation Notes

## Current Status (November 1, 2025)

### ✅ What's Working
1. **Shipping Costs**: Fully tracked from both eBay and Amazon
   - eBay: `ActualShippingCost` from GetOrders API
   - Amazon: `ShippingPrice.Amount` from Orders API
   - **61 eBay orders** currently have shipping data populated

2. **Taxes**: Tracked from both stores
   - eBay: `TotalTaxAmount` from GetOrders API
   - Amazon: `ItemTax.Amount` from Orders API

3. **Database Schema**: 
   - `seller_fee` column exists in `sold.db` orders table
   - `taxes` column exists
   - `shipping_cost` column exists

### ⚠️ Current Limitation: Seller Fees

**Both eBay and Amazon seller fees are currently $0.00** because:

#### eBay
- The **GetOrders API does NOT include FinalValueFee** data
- This is a known eBay API limitation
- Seller fees are only available through:
  - **eBay Selling Manager API** (requires separate API calls)
  - **GetAccount API** (requires additional permissions)
  - **eBay Seller Hub Reports** (manual download)

**eBay Fee Structure** (for reference):
- Final Value Fee: 12-15% of total sale (item price + shipping)
- Category-specific variations apply
- Promoted listings have additional fees (1-20%)
- Store subscription affects fee rates

#### Amazon  
- The **Orders API does NOT include seller fees**
- Amazon fees require the **Financial Events API**
- Financial Events API provides:
  - Commission fees
  - FBA fees (if using FBA)
  - Referral fees
  - Storage fees
  - Other marketplace fees

**Amazon Fee Structure** (for reference):
- Referral fee: 8-15% depending on category
- Closing fee: $1.80 for media items
- FBA fees: Variable (if using FBA)
- Storage fees: Monthly (if using FBA)

## Solutions

### Option 1: Estimated Fees (Quick)
Calculate estimated fees based on typical fee percentages:
```python
# eBay: 12.9% average (12% FV fee + 0.9% payment)
ebay_fee_estimate = (price + shipping) * 0.129

# Amazon: 15% average referral fee
amazon_fee_estimate = price * 0.15
```

### Option 2: Additional API Integration (Accurate)

#### For eBay:
```python
# Use GetSellerTransactions API with IncludeFinalValueFee=True
# Or integrate with eBay Seller Hub reporting
```

#### For Amazon:
```python
# Use Financial Events API
from sp_api.api import Finances
finances_api = Finances(credentials=credentials)
events = finances_api.list_financial_events(
    PostedAfter=start_date,
    PostedBefore=end_date
)
# Extract commission and fees from ShipmentEvent
```

### Option 3: Manual CSV Import
Both platforms allow downloading transaction reports with full fee breakdowns:
- eBay: Seller Hub → Reports → Transaction Report
- Amazon: Reports → Payments → Transaction View

## Recommendation

**Phase 1 (Immediate)**: Add estimated fees calculation
- Provides immediate profit insights
- Uses industry-standard percentages
- Add disclaimer on dashboard

**Phase 2 (Future)**: Integrate Financial APIs
- eBay: Selling Manager or Transaction API
- Amazon: Financial Events API
- Provides exact fee amounts

**Phase 3 (Optional)**: CSV import feature
- For historical data reconciliation
- Allows users to upload actual fee reports

## Code Changes Needed for Estimated Fees

### In `app.py` - eBay sync:
```python
# After price extraction:
estimated_fee = (float(price.text) + shipping_cost) * 0.129
'seller_fee': estimated_fee
```

### In `amazon_manager.py` - Amazon sync:
```python
# After price extraction:
seller_fee = price * 0.15  # Instead of hardcoded 0
```

### Add Dashboard Notice:
Add a small note in the Financial Analytics dashboard indicating fees are estimated.

## Testing Data

Current database shows:
- **172 total orders**
- **95 Amazon orders**: All with $0.00 seller fees
- **77 eBay orders**: All with $0.00 seller fees
- **61 orders**: Have shipping cost data (eBay only, synced Nov 1)

## Impact on Profit Calculations

Without accurate seller fees, profit margins are **over-estimated by 12-15%**.

Example for $100 sale:
- **Without fees**: $100 revenue - $50 cost = $50 profit (50% margin)
- **With estimated fees**: $100 revenue - $50 cost - $13 fees = $37 profit (37% margin)
- **Difference**: 13% margin over-estimation

This is significant for business decisions and should be addressed.
