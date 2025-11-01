"""
Enrich sold.db orders with LOT numbers and cost data from rawbol.db
This will dramatically improve profit tracking and analytics accuracy.
"""
import sqlite3

def enrich_sold_orders():
    print('=== Enriching Sold Orders with LOT and Cost Data ===\n')
    
    # Connect to databases
    sold_conn = sqlite3.connect('sold.db')
    sold_conn.row_factory = sqlite3.Row
    sold_cur = sold_conn.cursor()
    
    rawbol_conn = sqlite3.connect('rawbol.db')
    rawbol_conn.row_factory = sqlite3.Row
    rawbol_cur = rawbol_conn.cursor()
    
    # Get all sold orders
    sold_cur.execute('''
        SELECT order_id, item_id, barcode, lot_number
        FROM orders
        WHERE paid_time IS NOT NULL
    ''')
    orders = sold_cur.fetchall()
    
    print(f'Found {len(orders)} sold orders')
    
    # Statistics
    enriched_count = 0
    already_had_lot = 0
    no_match_found = 0
    
    for order in orders:
        order_id = order['order_id']
        upc = order['barcode'] or order['item_id']
        current_lot = order['lot_number']
        
        # Skip if already has lot_number
        if current_lot and str(current_lot).lower() not in ['', 'nan', 'none', 'null']:
            already_had_lot += 1
            continue
        
        if not upc:
            no_match_found += 1
            continue
        
        # Try to find matching item in rawbol
        rawbol_cur.execute('''
            SELECT lot_number, avg_cost, import_date
            FROM raw_bol_items
            WHERE upc = ? COLLATE NOCASE
            ORDER BY created_at DESC
            LIMIT 1
        ''', (upc,))
        
        rawbol_match = rawbol_cur.fetchone()
        
        if rawbol_match and rawbol_match['lot_number']:
            lot_number = rawbol_match['lot_number']
            
            # Update sold order with LOT number
            sold_cur.execute('''
                UPDATE orders
                SET lot_number = ?
                WHERE order_id = ?
            ''', (lot_number, order_id))
            
            enriched_count += 1
            
            if enriched_count % 10 == 0:
                print(f'  Enriched {enriched_count} orders...')
        else:
            no_match_found += 1
    
    # Commit changes
    sold_conn.commit()
    
    # Print results
    print(f'\n✅ Enrichment Complete!')
    print(f'   Already had LOT: {already_had_lot}')
    print(f'   Newly enriched: {enriched_count}')
    print(f'   No match found: {no_match_found}')
    print(f'   Total coverage: {already_had_lot + enriched_count}/{len(orders)} ({((already_had_lot + enriched_count) / len(orders) * 100):.1f}%)')
    
    # Show updated statistics
    print(f'\n=== Updated Statistics ===')
    sold_cur.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN lot_number IS NOT NULL AND lot_number != '' THEN 1 ELSE 0 END) as with_lot
        FROM orders
        WHERE paid_time IS NOT NULL
    ''')
    stats = sold_cur.fetchone()
    print(f"Total sold orders: {stats['total']}")
    print(f"Orders with LOT#: {stats['with_lot']} ({(stats['with_lot'] / stats['total'] * 100):.1f}%)")
    
    # Close connections
    sold_conn.close()
    rawbol_conn.close()
    
    return enriched_count

if __name__ == '__main__':
    enriched_count = enrich_sold_orders()
    print(f'\n🎉 Successfully enriched {enriched_count} sold orders!')
