"""
Test Amazon Catalog API image response
"""
import sqlite3
from amazon_manager import AmazonManager
import json

print("Testing Amazon Catalog API image retrieval...")
print("=" * 80)

# Get a few ASINs from amazonStore.db
conn = sqlite3.connect('amazonStore.db')
cur = conn.cursor()
cur.execute("SELECT ASIN, TITLE FROM ITEMS LIMIT 5")
asins = cur.fetchall()
conn.close()

amazon = AmazonManager()

for asin, title in asins:
    print(f"\nASIN: {asin}")
    print(f"Title: {title[:60]}...")
    
    catalog_data = amazon.get_catalog_item(asin)
    
    if catalog_data:
        # Print the full response to see structure
        print("Full response:")
        print(json.dumps(catalog_data, indent=2))
        
        # Look for images
        if 'images' in catalog_data:
            print("\nImages found:")
            print(json.dumps(catalog_data['images'], indent=2))
        else:
            print("\nNo 'images' key in response")
        
        print("\n" + "-" * 80)
    else:
        print("No catalog data returned")
    
    # Only test first one for now
    break

print("\nTest complete!")
