"""
Test script to verify Amazon Financial Events API is working
"""

from amazon_manager import AmazonManager

def test_financial_events():
    """Test fetching financial events from Amazon"""
    print("=" * 60)
    print("Testing Amazon Financial Events API")
    print("=" * 60)
    
    # Initialize Amazon manager
    amazon = AmazonManager()
    
    # Test fetching financial events for last 7 days
    print("\n1. Testing Financial Events API (last 7 days)...")
    financial_data = amazon.get_financial_events(days_back=7)
    
    if financial_data:
        print(f"\n✅ Successfully retrieved financial data for {len(financial_data)} orders")
        
        # Show sample data
        if len(financial_data) > 0:
            print("\n📊 Sample Financial Data:")
            print("-" * 60)
            for i, (order_id, data) in enumerate(list(financial_data.items())[:5]):
                print(f"\nOrder: {order_id}")
                print(f"  Seller Fee: ${data['seller_fee']:.2f}")
                print(f"  Shipping: ${data['shipping_cost']:.2f}")
                print(f"  Taxes: ${data['taxes']:.2f}")
                print(f"  Total Fees: ${data['total_fees']:.2f}")
                if i >= 4:  # Show max 5 samples
                    break
            
            if len(financial_data) > 5:
                print(f"\n... and {len(financial_data) - 5} more orders")
        
        # Calculate totals
        total_fees = sum(data['seller_fee'] for data in financial_data.values())
        total_shipping = sum(data['shipping_cost'] for data in financial_data.values())
        total_taxes = sum(data['taxes'] for data in financial_data.values())
        
        print("\n" + "=" * 60)
        print("TOTALS (Last 7 Days):")
        print(f"  Total Seller Fees: ${total_fees:.2f}")
        print(f"  Total Shipping: ${total_shipping:.2f}")
        print(f"  Total Taxes: ${total_taxes:.2f}")
        print("=" * 60)
    else:
        print("\n⚠️  No financial data retrieved (may not have any orders in last 7 days)")
    
    print("\n2. Testing full order sync with financial data...")
    synced_count = amazon.sync_orders_to_db(days_back=7)
    print(f"\n✅ Sync completed: {synced_count} orders")
    
    print("\n" + "=" * 60)
    print("Test completed!")
    print("=" * 60)

if __name__ == "__main__":
    test_financial_events()
