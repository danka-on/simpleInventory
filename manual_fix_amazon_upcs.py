import sqlite3

print("Manually fixing items where we found UPC matches in rawbol.db\n")

# Manual mappings based on search results
mappings = {
    'B0DX3VNNVQ': '37725617275',  # Noritake Bloomington Road Set of 4 Mugs Multi
    'B07B5SVLN8': '790824200228'   # Michael Aram Twig Gold Frame
}

sold_conn = sqlite3.connect('sold.db')
sold_cur = sold_conn.cursor()

amazon_conn = sqlite3.connect('amazonStore.db')
amazon_cur = amazon_conn.cursor()

for asin, upc in mappings.items():
    # Update amazonStore.db
    amazon_cur.execute('UPDATE ITEMS SET UPC = ? WHERE ASIN = ?', (upc, asin))
    print(f"✅ Updated amazonStore.db: {asin} → {upc}")
    
    # Update sold.db orders
    sold_cur.execute('UPDATE orders SET barcode = ? WHERE barcode = ? AND store = "amazon"', (upc, asin))
    updated = sold_cur.rowcount
    print(f"   Updated {updated} orders in sold.db")

sold_conn.commit()
amazon_conn.commit()

sold_conn.close()
amazon_conn.close()

print(f"\n✅ Fixed {len(mappings)} items with manual UPC mapping")
