"""
Test Rate Limiting for Amazon Catalog API

This verifies that the rate limiting is working correctly
"""

from amazon_manager import AmazonManager
import time

print("="*80)
print("TESTING AMAZON CATALOG API RATE LIMITING")
print("="*80)

# Initialize Amazon Manager
amazon = AmazonManager()

print(f"\n⚙️ Rate limit configuration:")
print(f"   - Delay between calls: {amazon.catalog_api_delay} seconds")
print(f"   - Max requests per second: ~{1/amazon.catalog_api_delay:.2f}")

# Test with a few ASINs
test_asins = ['B0DX3VNNVQ', 'B08N5WRWNW', 'B07YNK87NZ']

print(f"\n🔄 Testing with {len(test_asins)} ASINs...")
print("   (This should take at least ~{:.1f} seconds due to rate limiting)\n".format(
    len(test_asins) * amazon.catalog_api_delay
))

start_time = time.time()

for i, asin in enumerate(test_asins, 1):
    call_start = time.time()
    print(f"[{i}/{len(test_asins)}] Fetching {asin}...", end=" ")
    
    result = amazon.get_catalog_item(asin)
    
    call_duration = time.time() - call_start
    
    if result:
        print(f"✅ Success (took {call_duration:.2f}s)")
    else:
        print(f"❌ Failed (took {call_duration:.2f}s)")

total_time = time.time() - start_time

print(f"\n{'='*80}")
print(f"✅ Test complete!")
print(f"   - Total time: {total_time:.2f} seconds")
print(f"   - Average per call: {total_time/len(test_asins):.2f} seconds")
print(f"   - Rate limiting: {'WORKING' if total_time >= len(test_asins) * amazon.catalog_api_delay * 0.9 else 'NOT WORKING'}")
print("="*80)
