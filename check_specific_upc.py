"""
Check specific UPC: 047596288382
"""
import sqlite3

upc_to_check = "047596288382"

print(f"🔍 Checking UPC: {upc_to_check}")
print("=" * 80)

# Check in amazonStore.db
print("\n📦 Checking amazonStore.db:")
amazon = sqlite3.connect('amazonStore.db')
amazon_cur = amazon.cursor()

amazon_cur.execute("SELECT ASIN, UPC, TITLE, IMAGE FROM ITEMS WHERE UPC = ?", (upc_to_check,))
amazon_result = amazon_cur.fetchone()

if amazon_result:
    print(f"  ✅ Found in amazonStore.db")
    print(f"  ASIN: {amazon_result[0]}")
    print(f"  UPC: {amazon_result[1]}")
    print(f"  TITLE: {amazon_result[2]}")
    print(f"  IMAGE: {amazon_result[3] or 'NULL'}")
else:
    print(f"  ❌ NOT found in amazonStore.db")

amazon.close()

# Check in rawbol.db
print("\n📋 Checking rawbol.db:")
rawbol = sqlite3.connect('rawbol.db')
rawbol_cur = rawbol.cursor()

# Try exact match first
rawbol_cur.execute("SELECT upc, image_url FROM raw_bol_items WHERE upc = ?", (upc_to_check,))
rawbol_result = rawbol_cur.fetchone()

if rawbol_result:
    print(f"  ✅ Found with exact match")
    print(f"  UPC: {rawbol_result[0]}")
    print(f"  IMAGE_URL: {rawbol_result[1]}")
else:
    print(f"  ❌ NOT found with exact match: '{upc_to_check}'")
    
    # Try without leading zero
    upc_no_leading_zero = upc_to_check.lstrip('0')
    print(f"\n  🔄 Trying without leading zero: '{upc_no_leading_zero}'")
    rawbol_cur.execute("SELECT upc, image_url FROM raw_bol_items WHERE upc = ?", (upc_no_leading_zero,))
    rawbol_result2 = rawbol_cur.fetchone()
    
    if rawbol_result2:
        print(f"  ✅ Found without leading zero!")
        print(f"  UPC in rawbol: {rawbol_result2[0]}")
        print(f"  IMAGE_URL: {rawbol_result2[1]}")
    else:
        print(f"  ❌ NOT found without leading zero either")

rawbol.close()

print("\n" + "=" * 80)
print("💡 If UPC formats don't match (with/without leading zeros), that's the issue!")
