"""
Test eBay payout sync function
"""

from ebay_manager import EbayManager

def test_ebay_payouts():
    """Test eBay payout sync with graceful OAuth error handling"""
    print("=" * 80)
    print("Testing eBay Payout Sync")
    print("=" * 80)
    
    ebay = EbayManager()
    count = ebay.sync_payouts_to_db(days_back=90)
    
    print("\n" + "=" * 80)
    print(f"RESULT: Synced {count} eBay payouts")
    print("=" * 80)

if __name__ == '__main__':
    test_ebay_payouts()
