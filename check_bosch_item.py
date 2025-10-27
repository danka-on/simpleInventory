"""
Check details for Bosch SR0827X item
"""
import sqlite3
import json

# Check sold.db
conn = sqlite3.connect('sold.db')
cur = conn.cursor()

cur.execute("SELECT * FROM orders WHERE title LIKE '%Bosch SR0827X%'")
cols = [desc[0] for desc in cur.description]
row = cur.fetchone()

if row:
    order = dict(zip(cols, row))
    print("Found in sold.db:")
    print(json.dumps(order, indent=2, default=str))
    
    item_id = order.get('item_id')
    barcode = order.get('barcode')
    
    print(f"\nItemID: {item_id}")
    print(f"Barcode: {barcode}")
    
    # Check if this ItemID exists in ebayStore.db
    if item_id:
        ebay_conn = sqlite3.connect('ebayStore.db')
        ebay_cur = ebay_conn.cursor()
        ebay_cur.execute("SELECT Title, ItemID, Image, UPC FROM INVENTORY WHERE ItemID = ?", (item_id,))
        ebay_row = ebay_cur.fetchone()
        
        if ebay_row:
            print("\nFound in ebayStore.db:")
            print(f"  Title: {ebay_row[0]}")
            print(f"  ItemID: {ebay_row[1]}")
            print(f"  Image: {ebay_row[2]}")
            print(f"  UPC: {ebay_row[3]}")
        else:
            print(f"\n❌ ItemID {item_id} NOT found in ebayStore.db")
        
        ebay_conn.close()
    
    # Check if barcode exists in rawbol.db
    if barcode:
        rawbol_conn = sqlite3.connect('rawbol.db')
        rawbol_cur = rawbol_conn.cursor()
        rawbol_cur.execute("SELECT upc, image_url FROM raw_bol_items WHERE upc = ?", (barcode,))
        rawbol_row = rawbol_cur.fetchone()
        
        if rawbol_row:
            print("\nFound in rawbol.db:")
            print(f"  UPC: {rawbol_row[0]}")
            print(f"  Image: {rawbol_row[1]}")
        else:
            print(f"\n❌ Barcode {barcode} NOT found in rawbol.db")
        
        rawbol_conn.close()
else:
    print("❌ Not found in sold.db")

conn.close()
