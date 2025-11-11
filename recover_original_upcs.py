import sqlite3

asins = ['B00I4EMDUE', 'B089TR3GL1', 'B0CBCV19TR', 'B0BSN3S8TD', 'B002EI2XS8']

print("Finding original UPCs from order history...\n")

sold_conn = sqlite3.connect('sold.db')
sold_cur = sold_conn.cursor()

original_upcs = {}

for asin in asins:
    # Get ALL orders for this ASIN/ItemID
    sold_cur.execute('''
        SELECT order_id, barcode, paid_time 
        FROM orders 
        WHERE item_id = ? 
        ORDER BY paid_time ASC
    ''', (asin,))
    
    results = sold_cur.fetchall()
    
    if results:
        print(f"\n{asin} - Order history ({len(results)} orders):")
        
        # Find the first order with a non-ASIN barcode
        for order_id, barcode, paid_time in results:
            is_asin = barcode and barcode.startswith('B0') and len(barcode) == 10
            marker = "  🔴 ASIN" if is_asin else "  ✅ UPC"
            print(f"  {paid_time[:10]}: {barcode} {marker}")
            
            # Save the first valid UPC we find
            if not is_asin and barcode and asin not in original_upcs:
                original_upcs[asin] = barcode

sold_conn.close()

print("\n\n📋 Original UPCs found:")
for asin, upc in original_upcs.items():
    print(f"  {asin} → {upc}")

print(f"\n✅ Found {len(original_upcs)} out of {len(asins)} original UPCs")

# Save to file for the fix script
if original_upcs:
    with open('recovered_upcs.txt', 'w') as f:
        for asin, upc in original_upcs.items():
            f.write(f"{asin},{upc}\n")
    print(f"\n💾 Saved to recovered_upcs.txt")
