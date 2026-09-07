"""
Amazon Returns API Integration - Extension for amazon_manager.py

Add this method to the AmazonManager class
"""

import time
from sp_api.api import Finances
from sp_api.base import SellingApiException

def get_returns(self, days_back=90):
    """
    Fetch returns from Amazon Returns API
    Returns list of return items with financial details
    """
    try:
        # Note: Amazon Returns API uses Orders API with return-specific parameters
        # We need to check RefundEventList in Financial Events API instead
        
        from datetime import datetime, timedelta
        
        finances_api = Finances(credentials=self.credentials, marketplace=self.marketplace)
        
        posted_after = (datetime.utcnow() - timedelta(days=days_back)).isoformat()
        
        print(f"🔄 Fetching Amazon returns from last {days_back} days...")
        
        all_refund_events = []
        next_token = None
        page = 1
        
        while True:
            try:
                if next_token:
                    response = finances_api.list_financial_events(NextToken=next_token)
                else:
                    response = finances_api.list_financial_events(
                        PostedAfter=posted_after,
                        MaxResultsPerPage=100
                    )
                
                if response.errors:
                    print(f"❌ Error fetching returns (page {page}): {response.errors}")
                    break
                
                payload = response.payload
                financial_events = payload.get('FinancialEvents', {})
                refund_events = financial_events.get('RefundEventList', [])
                
                all_refund_events.extend(refund_events)
                
                print(f"  📄 Page {page}: Retrieved {len(refund_events)} refund events")
                
                next_token = payload.get('NextToken')
                if not next_token:
                    break
                
                page += 1
                time.sleep(1)
                
            except SellingApiException as e:
                if 'QuotaExceeded' in str(e):
                    print(f"⚠️  Rate limit hit, waiting 5 seconds...")
                    time.sleep(5)
                    continue
                else:
                    print(f"❌ Error fetching returns: {e}")
                    break
        
        print(f"\n✅ Retrieved {len(all_refund_events)} return events")
        
        # Process return events to extract key data
        returns_data = []
        
        for event in all_refund_events:
            amazon_order_id = event.get('AmazonOrderId')
            posted_date = event.get('PostedDate')
            
            # Get item details from ShipmentItemAdjustmentList
            items = event.get('ShipmentItemAdjustmentList', [])
            
            for item in items:
                order_item_id = item.get('OrderItemId')
                quantity_shipped = item.get('QuantityShipped', 0)
                
                # Extract refund amount from ItemChargeAdjustmentList
                refund_amount = 0
                item_charges = item.get('ItemChargeAdjustmentList', [])
                for charge in item_charges:
                    charge_type = charge.get('ChargeType', '')
                    if charge_type == 'Principal':  # Main refund amount
                        amount = charge.get('ChargeAmount', {}).get('CurrencyAmount', 0)
                        refund_amount += abs(float(amount))
                
                # Extract fees refunded
                seller_fee_refund = 0
                item_fees = item.get('ItemFeeAdjustmentList', [])
                for fee in item_fees:
                    fee_type = fee.get('FeeType', '')
                    if fee_type in ['Commission', 'RefundCommission', 'ReferralFee']:
                        fee_amount = fee.get('FeeAmount', {}).get('CurrencyAmount', 0)
                        seller_fee_refund += abs(float(fee_amount))
                
                # Extract return shipping cost (if seller paid for return label)
                return_shipping_cost = 0
                for charge in item_charges:
                    charge_type = charge.get('ChargeType', '')
                    if charge_type in ['Shipping', 'ShippingCharge']:
                        amount = charge.get('ChargeAmount', {}).get('CurrencyAmount', 0)
                        return_shipping_cost += abs(float(amount))
                
                returns_data.append({
                    'order_id': amazon_order_id,
                    'item_id': order_item_id,
                    'return_date': posted_date,
                    'quantity': quantity_shipped,
                    'refund_amount': refund_amount,
                    'seller_fee_refund': seller_fee_refund,
                    'return_shipping_cost': return_shipping_cost
                })
        
        return returns_data
        
    except Exception as e:
        print(f"❌ Error in get_returns: {e}")
        import traceback
        traceback.print_exc()
        return []
