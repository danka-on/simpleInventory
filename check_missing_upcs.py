"""
Check UPCs: 047596043974 and 028851588270
"""
import sqlite3

upcs = ["047596043974", "028851588270"]

for upc in upcs:
    print(f"\n{'='*80}")
    print(f"🔍 Checking UPC: {upc}")
    print(f"{'='*80}")
    
    # Check ebayStore.db
    print("\n📦 Checking ebayStore.db:")
    ebay = sqlite3.connect('ebayStore.db')
    ebay_cur = ebay.cursor()
    ebay_cur.execute("SELECT ItemID, UPC, Title, Image FROM INVENTORY WHERE UPC = ?", (upc,))
    ebay_result = ebay_cur.fetchone()
    if ebay_result:
        print(f"  ✅ Found in ebayStore.db")
        print(f"  ItemID: {ebay_result[0]}")
        print(f"  UPC: {ebay_result[1]}")
        print(f"  Title: {ebay_result[2][:60]}..." if ebay_result[2] and len(ebay_result[2]) > 60 else f"  Title: {ebay_result[2]}")
        print(f"  Image: {ebay_result[3][:80]}..." if ebay_result[3] and len(ebay_result[3]) > 80 else f"  Image: {ebay_result[3] or 'NULL'}")
    else:
        print(f"  ❌ NOT found in ebayStore.db with exact UPC")
    ebay.close()
    
    # Check sold.db
    print("\n📮 Checking sold.db:")
    sold = sqlite3.connect('sold.db')
    sold_cur = sold.cursor()
    sold_cur.execute("SELECT order_id, item_id, barcode, title, image, store FROM orders WHERE barcode = ?", (upc,))
    sold_results = sold_cur.fetchall()
    if sold_results:
        print(f"  ✅ Found {len(sold_results)} order(s) in sold.db")
        for result in sold_results:
            print(f"\n  Order ID: {result[0]}")
            print(f"  Item ID: {result[1]}")
            print(f"  Barcode: {result[2]}")
            print(f"  Title: {result[3][:60]}..." if result[3] and len(result[3]) > 60 else f"  Title: {result[3]}")
            print(f"  Image: {result[4][:80]}..." if result[4] and len(result[4]) > 80 else f"  Image: {result[4] or 'NULL'}")
            print(f"  Store: {result[5]}")
            
            # If we have item_id, check ebayStore by ItemID
            if result[1]:
                ebay2 = sqlite3.connect('ebayStore.db')
                ebay2_cur = ebay2.cursor()
                ebay2_cur.execute("SELECT Image FROM INVENTORY WHERE ItemID = ?", (result[1],))
                ebay_by_itemid = ebay2_cur.fetchone()
                if ebay_by_itemid:
                    print(f"  📸 ebayStore.db has image for ItemID {result[1]}: {ebay_by_itemid[0][:80] if ebay_by_itemid[0] else 'NULL'}...")
                ebay2.close()
    else:
        print(f"  ❌ NOT found in sold.db")
    sold.close()
    
    # Check rawbol.db
    print("\n📋 Checking rawbol.db:")
    rawbol = sqlite3.connect('rawbol.db')
    rawbol_cur = rawbol.cursor()
    
    # Try exact match
    rawbol_cur.execute("SELECT upc, image_url FROM raw_bol_items WHERE upc = ?", (upc,))
    rawbol_result = rawbol_cur.fetchone()
    
    if rawbol_result:
        print(f"  ✅ Found in rawbol.db (exact match)")
        print(f"  UPC: {rawbol_result[0]}")
        print(f"  Image URL: {rawbol_result[1][:80]}..." if rawbol_result[1] and len(rawbol_result[1]) > 80 else f"  Image URL: {rawbol_result[1]}")
    else:
        # Try without leading zero
        upc_no_zero = upc.lstrip('0')
        rawbol_cur.execute("SELECT upc, image_url FROM raw_bol_items WHERE upc = ?", (upc_no_zero,))
        rawbol_result2 = rawbol_cur.fetchone()
        if rawbol_result2:
            print(f"  ✅ Found in rawbol.db (without leading zero)")
            print(f"  UPC: {rawbol_result2[0]}")
            print(f"  Image URL: {rawbol_result2[1][:80]}..." if rawbol_result2[1] and len(rawbol_result2[1]) > 80 else f"  Image URL: {rawbol_result2[1]}")
        else:
            print(f"  ❌ NOT found in rawbol.db")
    rawbol.close()

print("\n" + "="*80)
