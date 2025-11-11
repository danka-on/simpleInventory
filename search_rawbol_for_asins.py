import sqlite3

# Connect to sold.db to get titles for ASINs
sold_conn = sqlite3.connect('sold.db')
sold_cur = sold_conn.cursor()

# Get the 7 orders with ASIN barcodes and their titles
asins = ['B00I4EMDUE', 'B089TR3GL1', 'B0CBCV19TR', 'B0DX3VNNVQ', 'B0BSN3S8TD', 'B002EI2XS8', 'B07B5SVLN8']

print("Checking if we have UPCs for these items in rawbol.db:\n")

rawbol_conn = sqlite3.connect('rawbol.db')
rawbol_cur = rawbol_conn.cursor()

for asin in asins:
    # Get title from sold.db
    sold_cur.execute('SELECT title FROM orders WHERE barcode = ? AND store = "amazon" LIMIT 1', (asin,))
    title_result = sold_cur.fetchone()
    title = title_result[0] if title_result else "Unknown"
    
    print(f"\nASIN: {asin}")
    print(f"Title: {title[:60]}...")
    
    # Search rawbol for similar title
    search_words = title.split()[:3]  # First 3 words
    search_pattern = '%' + '%'.join(search_words) + '%'
    
    rawbol_cur.execute('SELECT upc, item_description FROM raw_bol_items WHERE item_description LIKE ? LIMIT 5', (search_pattern,))
    results = rawbol_cur.fetchall()
    
    if results:
        print(f"  Found {len(results)} possible matches in rawbol:")
        for upc, desc in results:
            print(f"    UPC: {upc} - {desc[:60]}")
    else:
        print(f"  ❌ No matches found in rawbol.db")

sold_conn.close()
rawbol_conn.close()
