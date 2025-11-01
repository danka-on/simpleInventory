"""
Backfill UPCs for Amazon sold items that still have ASIN as barcode
This updates older sold items to have proper UPC barcodes from amazonStore.db
"""
import sqlite3

def backfill_amazon_upc_barcodes():
    print('=== Backfilling Amazon Sold Item Barcodes ===\n')
    
    # Connect to databases
    sold_conn = sqlite3.connect('sold.db')
    sold_cur = sold_conn.cursor()
    
    amazon_conn = sqlite3.connect('amazonStore.db')
    amazon_cur = amazon_conn.cursor()
    
    # Find Amazon items where barcode is still an ASIN
    sold_cur.execute('''
        SELECT id, order_id, title, barcode, item_id
        FROM orders
        WHERE store = "amazon" 
        AND (barcode LIKE "B0%" OR LENGTH(barcode) = 10)
    ''')
    
    asin_items = sold_cur.fetchall()
    
    print(f'Found {len(asin_items)} items with ASIN as barcode\n')
    
    updated_count = 0
    no_upc_count = 0
    
    for row in asin_items:
        db_id, order_id, title, asin_barcode, item_id = row
        
        # Look up UPC in amazonStore.db using the ASIN
        amazon_cur.execute('SELECT UPC FROM ITEMS WHERE ASIN = ?', (asin_barcode,))
        amazon_row = amazon_cur.fetchone()
        
        if amazon_row and amazon_row[0] and amazon_row[0] != asin_barcode:
            upc = amazon_row[0]
            
            # Update the barcode in sold.db
            sold_cur.execute('UPDATE orders SET barcode = ? WHERE id = ?', (upc, db_id))
            
            updated_count += 1
            print(f'✅ Updated {order_id}')
            print(f'   {title[:50]}...')
            print(f'   {asin_barcode} → {upc}')
            print()
        else:
            no_upc_count += 1
            print(f'⚠️ No UPC for {order_id}: {asin_barcode}')
    
    # Commit changes
    sold_conn.commit()
    
    # Close connections
    sold_conn.close()
    amazon_conn.close()
    
    # Summary
    print(f'\n=== Summary ===')
    print(f'✅ Updated: {updated_count} items')
    print(f'⚠️ No UPC available: {no_upc_count} items')
    print(f'📊 Success rate: {(updated_count / len(asin_items) * 100):.1f}%')
    
    return updated_count

if __name__ == '__main__':
    updated = backfill_amazon_upc_barcodes()
    print(f'\n🎉 Backfill complete! Updated {updated} sold items with UPC barcodes')
