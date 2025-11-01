"""
Check the Noritake item in sold orders and amazonStore
"""
import sqlite3

# Check sold.db
sold_conn = sqlite3.connect('sold.db')
sold_conn.row_factory = sqlite3.Row
sold_cur = sold_conn.cursor()

sold_cur.execute("""
    SELECT id, order_id, item_id, title, image, barcode
    FROM orders 
    WHERE title LIKE '%Noritake Colorwave Graphite%'
    LIMIT 1
""")
row = sold_cur.fetchone()

if row:
    print(f"📦 Sold Order Found:")
    print(f"   ID: {row['id']}")
    print(f"   Order ID: {row['order_id']}")
    print(f"   ASIN: {row['item_id']}")
    print(f"   Barcode/UPC: {row['barcode']}")
    print(f"   Title: {row['title']}")
    print(f"   Image: {row['image'] or 'NO IMAGE'}")
    
    # Check amazonStore.db
    amazon_conn = sqlite3.connect('amazonStore.db')
    amazon_conn.row_factory = sqlite3.Row
    amazon_cur = amazon_conn.cursor()
    
    # Try by ASIN
    amazon_cur.execute("SELECT ASIN, UPC, IMAGE FROM ITEMS WHERE ASIN = ? LIMIT 1", (row['item_id'],))
    arow = amazon_cur.fetchone()
    
    if arow:
        print(f"\n📦 Amazon Store (by ASIN):")
        print(f"   ASIN: {arow['ASIN']}")
        print(f"   UPC: {arow['UPC']}")
        print(f"   IMAGE: {arow['IMAGE'] or 'NO IMAGE'}")
    else:
        print(f"\n❌ Not found in amazonStore.db by ASIN: {row['item_id']}")
        
        # Try by UPC
        if row['barcode']:
            amazon_cur.execute("SELECT ASIN, UPC, IMAGE FROM ITEMS WHERE UPC = ? LIMIT 1", (row['barcode'],))
            arow = amazon_cur.fetchone()
            if arow:
                print(f"\n📦 Amazon Store (by UPC):")
                print(f"   ASIN: {arow['ASIN']}")
                print(f"   UPC: {arow['UPC']}")
                print(f"   IMAGE: {arow['IMAGE'] or 'NO IMAGE'}")
            else:
                print(f"❌ Not found in amazonStore.db by UPC: {row['barcode']}")
    
    amazon_conn.close()
else:
    print("❌ Noritake item not found in sold orders")

sold_conn.close()
