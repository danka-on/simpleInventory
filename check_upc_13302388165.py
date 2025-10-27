"""
Check UPC 013302388165 across all databases
"""
import sqlite3

upc = "013302388165"

print(f"🔍 Checking UPC: {upc}")
print("=" * 80)

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
    print(f"  ❌ NOT found in ebayStore.db")
ebay.close()

# Check sold.db
print("\n📮 Checking sold.db (ready to ship orders):")
sold = sqlite3.connect('sold.db')
sold_cur = sold.cursor()
sold_cur.execute("SELECT order_id, barcode, title, image, store, isHandled FROM orders WHERE barcode = ?", (upc,))
sold_results = sold_cur.fetchall()
if sold_results:
    print(f"  ✅ Found {len(sold_results)} order(s) in sold.db")
    for result in sold_results:
        print(f"\n  Order ID: {result[0]}")
        print(f"  Barcode: {result[1]}")
        print(f"  Title: {result[2][:60]}..." if result[2] and len(result[2]) > 60 else f"  Title: {result[2]}")
        print(f"  Image: {result[3][:80]}..." if result[3] and len(result[3]) > 80 else f"  Image: {result[3] or 'NULL'}")
        print(f"  Store: {result[4]}")
        print(f"  isHandled: {result[5]}")
else:
    print(f"  ❌ NOT found in sold.db")
sold.close()

# Check rawbol.db
print("\n📋 Checking rawbol.db:")
rawbol = sqlite3.connect('rawbol.db')
rawbol_cur = rawbol.cursor()
rawbol_cur.execute("SELECT upc, image_url FROM raw_bol_items WHERE upc = ?", (upc,))
rawbol_result = rawbol_cur.fetchone()
if rawbol_result:
    print(f"  ✅ Found in rawbol.db")
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

print("\n" + "=" * 80)
print("💡 If sold.db has NULL image but ebayStore.db has image, sold orders need enrichment!")
