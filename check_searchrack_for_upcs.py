import sqlite3

# These are the 5 ASINs that currently have ASIN as barcode in sold.db
asins_to_check = {
    'B00I4EMDUE': 'Lenox Sophisticate Salad Plate',
    'B089TR3GL1': 'Lenox Butterfly Meadow Tea Mug',
    'B0CBCV19TR': 'Sunbrella Curtains',
    'B0BSN3S8TD': 'Certified International Birdhouse',
    'B002EI2XS8': 'Villeroy & Boch Flatware Set'
}

print("Checking searchRack.db for items with these ASINs as item_id...\n")

conn = sqlite3.connect('searchRack.db')
cur = conn.cursor()

# Check if rack table exists and has the columns we need
cur.execute("PRAGMA table_info(rack)")
columns = [row[1] for row in cur.fetchall()]
print(f"Available columns: {columns}\n")

found_count = 0

for asin, title in asins_to_check.items():
    # Try searching by itemid
    if 'itemid' in columns:
        cur.execute('SELECT barcode, title, area_code, quantity FROM rack WHERE itemid = ?', (asin,))
        results = cur.fetchall()
        
        if results:
            print(f"✅ Found {asin} in searchRack.db:")
            for barcode, title, area, qty in results:
                print(f"   Barcode: {barcode}")
                print(f"   Title: {title}")
                print(f"   Location: {area}")
                print(f"   Quantity: {qty}\n")
            found_count += 1
        else:
            print(f"❌ {asin} not found in searchRack.db")
    
    # Also try searching by title
    search_words = title.split()[:3]
    search_pattern = '%' + '%'.join(search_words) + '%'
    cur.execute('SELECT barcode, title, area_code FROM rack WHERE title LIKE ? LIMIT 3', (search_pattern,))
    title_results = cur.fetchall()
    
    if title_results:
        print(f"   📋 Found by title search:")
        for barcode, t, area in title_results:
            print(f"      Barcode: {barcode} - {t[:50]}")

conn.close()

print(f"\n📊 Found {found_count} items with proper barcodes in searchRack.db")
