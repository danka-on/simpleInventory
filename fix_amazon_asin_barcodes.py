"""
Fix Amazon orders in sold.db that have ASINs as barcodes
when the correct UPC is available in amazonStore.db
"""
import sqlite3

def is_asin(value):
    """Check if a value looks like an ASIN (B0XXXXXXXXX - starts with B0, 10 chars)"""
    if not value:
        return False
    s = str(value)
    return s.startswith('B0') and len(s) == 10

def main():
    print("🔍 Checking for Amazon orders with ASIN barcodes that can be fixed...")
    
    # Connect to both databases
    sold_conn = sqlite3.connect('sold.db')
    sold_cur = sold_conn.cursor()
    
    amazon_conn = sqlite3.connect('amazonStore.db')
    amazon_cur = amazon_conn.cursor()
    
    # Find Amazon orders where barcode looks like ASIN
    sold_cur.execute('''
        SELECT id, order_id, item_id, barcode 
        FROM orders 
        WHERE store = 'amazon' 
        AND barcode IS NOT NULL
        AND barcode LIKE 'B0%'
    ''')
    
    orders_with_asins = sold_cur.fetchall()
    
    if not orders_with_asins:
        print("✅ No orders found with ASIN barcodes")
        sold_conn.close()
        amazon_conn.close()
        return
    
    print(f"📋 Found {len(orders_with_asins)} orders with ASIN barcodes")
    
    fixed_count = 0
    skipped_count = 0
    
    for order_id, amazon_order_id, item_id, current_barcode in orders_with_asins:
        # Verify it's actually an ASIN format
        if not is_asin(current_barcode):
            continue
        
        # Look up in amazonStore using item_id (which should be ASIN)
        asin = item_id or current_barcode
        
        amazon_cur.execute('SELECT UPC FROM ITEMS WHERE ASIN = ?', (asin,))
        result = amazon_cur.fetchone()
        
        if result and result[0]:
            new_upc = result[0]
            
            # Make sure the new UPC is not also an ASIN
            if is_asin(new_upc):
                print(f"⏭️  Order {amazon_order_id}: UPC in amazonStore is still ASIN ({new_upc})")
                skipped_count += 1
                continue
            
            # Update the barcode
            sold_cur.execute('UPDATE orders SET barcode = ? WHERE id = ?', (new_upc, order_id))
            print(f"✅ Order {amazon_order_id}: {current_barcode} → {new_upc}")
            fixed_count += 1
        else:
            print(f"⚠️  Order {amazon_order_id}: No UPC found in amazonStore for ASIN {asin}")
            skipped_count += 1
    
    sold_conn.commit()
    sold_conn.close()
    amazon_conn.close()
    
    print(f"\n📊 Summary:")
    print(f"   ✅ Fixed: {fixed_count}")
    print(f"   ⏭️  Skipped: {skipped_count}")
    print(f"   📋 Total checked: {len(orders_with_asins)}")

if __name__ == '__main__':
    main()
