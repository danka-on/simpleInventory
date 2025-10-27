"""
Test script to fetch and display Amazon listings
"""

from amazon_manager import AmazonManager

print("🔄 Testing Amazon Listings Sync\n")

manager = AmazonManager()

print("Step 1: Fetching listings from Amazon...")
print("(This requests a report from Amazon and waits for it to be ready)\n")

count = manager.sync_listings_to_db()

if count > 0:
    print(f"\n✅ Successfully synced {count} listings!")
    
    # Show sample from database
    import sqlite3
    conn = sqlite3.connect('amazonStore.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cur.execute('SELECT COUNT(*) as total FROM ITEMS')
    total = cur.fetchone()['total']
    
    cur.execute('SELECT * FROM ITEMS LIMIT 5')
    items = cur.fetchall()
    
    print(f"\n📊 Database Status:")
    print(f"   Total items: {total}")
    
    if items:
        print(f"\n📦 Sample listings:")
        for i, item in enumerate(items, 1):
            print(f"\n{i}. {item['TITLE'][:60]}...")
            print(f"   ASIN: {item['ASIN']}")
            print(f"   SKU: {item['SKU']}")
            print(f"   Price: ${item['PRICE']}")
            print(f"   Quantity: {item['QUANTITY']}")
            print(f"   Status: {item['STATUS']}")
            if item['UPC']:
                print(f"   UPC: {item['UPC']}")
    
    conn.close()
else:
    print("\n⚠️ No listings synced. Check the output above for errors.")

print("\n" + "="*60)
print("You can now view these in SearchRack by selecting 'Amazon Store'")
print("="*60)
