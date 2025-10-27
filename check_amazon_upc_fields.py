"""
Check additional Amazon fields for UPC codes
Examines: asin2, asin3, listing-id, item-description, item-note
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from amazon_manager import AmazonManager

def check_upc_fields():
    """Check additional fields that might contain UPC codes"""
    
    print("Fetching Amazon inventory report...")
    manager = AmazonManager()
    
    try:
        listings = manager.get_active_listings()
        print(f"\nFetched {len(listings)} listings from Amazon")
        print("=" * 80)
        
        # Track which fields have data
        field_stats = {
            'asin2': 0,
            'asin3': 0,
            'listing-id': 0,
            'item-description': 0,
            'item-note': 0,
            'product-id': 0,  # For comparison
            'external-product-id': 0  # For comparison
        }
        
        # Sample items with data in each field
        samples = {field: [] for field in field_stats.keys()}
        
        # Check each listing
        for listing in listings:
            for field in field_stats.keys():
                value = listing.get(field, '').strip()
                if value:
                    field_stats[field] += 1
                    if len(samples[field]) < 3:  # Keep 3 samples per field
                        samples[field].append({
                            'sku': listing.get('seller-sku', 'N/A'),
                            'title': listing.get('item-name', 'N/A')[:50],
                            'value': value
                        })
        
        # Display results
        print("\nField Coverage:")
        print("-" * 80)
        for field, count in field_stats.items():
            percentage = (count / len(listings) * 100) if listings else 0
            print(f"{field:25} {count:4}/{len(listings)} ({percentage:5.1f}%)")
        
        # Display samples
        print("\n" + "=" * 80)
        print("SAMPLE VALUES:")
        print("=" * 80)
        
        for field in ['asin2', 'asin3', 'listing-id', 'item-description', 'item-note']:
            print(f"\n{field.upper()}:")
            print("-" * 80)
            if samples[field]:
                for i, sample in enumerate(samples[field], 1):
                    print(f"\nSample {i}:")
                    print(f"  SKU: {sample['sku']}")
                    print(f"  Title: {sample['title']}")
                    print(f"  Value: {sample['value'][:200]}")  # Truncate long values
            else:
                print("  (No data in this field)")
        
        # Look for UPC patterns (12-13 digits)
        print("\n" + "=" * 80)
        print("SEARCHING FOR UPC PATTERNS (12-13 digit numbers):")
        print("=" * 80)
        
        import re
        upc_pattern = re.compile(r'\b\d{12,13}\b')
        
        upc_found = {
            'asin2': [],
            'asin3': [],
            'listing-id': [],
            'item-description': [],
            'item-note': []
        }
        
        for listing in listings:
            for field in upc_found.keys():
                value = listing.get(field, '')
                matches = upc_pattern.findall(str(value))
                if matches and len(upc_found[field]) < 5:  # Keep 5 examples
                    upc_found[field].append({
                        'sku': listing.get('seller-sku', 'N/A'),
                        'title': listing.get('item-name', 'N/A')[:50],
                        'upcs': matches
                    })
        
        for field, items in upc_found.items():
            print(f"\n{field.upper()} - Found UPC patterns:")
            if items:
                for item in items:
                    print(f"  SKU: {item['sku']}")
                    print(f"  Title: {item['title']}")
                    print(f"  UPCs: {', '.join(item['upcs'])}")
                    print()
            else:
                print("  (No UPC patterns found)")
        
        # Summary
        print("\n" + "=" * 80)
        print("SUMMARY:")
        print("=" * 80)
        total_with_upcs = sum(1 for items in upc_found.values() if items)
        print(f"Fields with UPC patterns: {total_with_upcs}/5")
        for field, items in upc_found.items():
            if items:
                print(f"  - {field}: {len(items)} examples found")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_upc_fields()
