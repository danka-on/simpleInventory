import sqlite3

asins = ['B00I4EMDUE', 'B089TR3GL1', 'B0CBCV19TR', 'B0BSN3S8TD', 'B002EI2XS8']

print("Checking ebayStore.db for these items...\n")

try:
    conn = sqlite3.connect('ebayStore.db')
    cur = conn.cursor()
    
    # Get table structure
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cur.fetchall()
    print(f"Tables: {[t[0] for t in tables]}\n")
    
    # Check if ITEMS table has UPC column
    if ('ITEMS',) in tables:
        cur.execute("PRAGMA table_info(ITEMS)")
        columns = [col[1] for col in cur.fetchall()]
        print(f"ITEMS columns: {columns}\n")
        
        # Search by ItemID
        for asin in asins:
            cur.execute('SELECT ItemID, UPC, Title FROM ITEMS WHERE ItemID = ?', (asin,))
            result = cur.fetchone()
            if result:
                print(f"✅ Found {asin}: UPC = {result[1]}, Title: {result[2][:50]}")
    
    conn.close()
except Exception as e:
    print(f"Error: {e}")

# Now let's check sold.db history to see if there's old data
print("\n\nChecking sold.db for historical barcode data...")
sold_conn = sqlite3.connect('sold.db')
sold_cur = sold_conn.cursor()

# Get the order history for these ASINs - maybe there's a column we're missing
for asin in asins:
    sold_cur.execute('SELECT order_id, item_id, barcode, title FROM orders WHERE item_id = ? OR barcode = ?', (asin, asin))
    results = sold_cur.fetchall()
    if results:
        print(f"\n{asin}:")
        for order_id, item_id, barcode, title in results:
            print(f"  Order: {order_id}, ItemID: {item_id}, Barcode: {barcode}")

sold_conn.close()
