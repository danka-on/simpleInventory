"""
Script to add lot_number column to sold.db and enrich it from bol.db
"""
import sqlite3

def enrich_sold_lot_numbers():
    """
    1. Add lot_number column to sold.db orders table if it doesn't exist
    2. Enrich all sold orders with lot_number from rawbol.db by matching UPC
    """
    
    # Connect to databases
    sold_conn = sqlite3.connect('sold.db')
    sold_cur = sold_conn.cursor()
    
    rawbol_conn = sqlite3.connect('rawbol.db')
    rawbol_conn.row_factory = sqlite3.Row
    rawbol_cur = rawbol_conn.cursor()
    
    try:
        # Step 1: Check if lot_number column exists, if not add it
        sold_cur.execute("PRAGMA table_info(orders)")
        columns = [col[1] for col in sold_cur.fetchall()]
        
        if 'lot_number' not in columns:
            print("Adding lot_number column to sold.db orders table...")
            sold_cur.execute("ALTER TABLE orders ADD COLUMN lot_number TEXT")
            sold_conn.commit()
            print("✓ Column added successfully")
        else:
            print("✓ lot_number column already exists")
        
        # Step 2: Enrich all sold orders with lot_number from rawbol.db
        print("\nEnriching sold orders with LOT # from rawbol.db...")
        
        # Get all sold orders
        sold_cur.execute("SELECT order_id, barcode, item_id FROM orders")
        orders = sold_cur.fetchall()
        
        updated_count = 0
        skipped_count = 0
        
        for order in orders:
            order_id = order[0]
            barcode = order[1]
            item_id = order[2]
            
            # Try to find lot_number from rawbol.db using UPC (barcode or item_id)
            upc = barcode or item_id
            
            if upc:
                rawbol_cur.execute("SELECT lot_number FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1", (upc,))
                rawbol_row = rawbol_cur.fetchone()
                
                if rawbol_row and rawbol_row['lot_number']:
                    lot_num = rawbol_row['lot_number']
                    if str(lot_num).lower() not in ['', 'nan', 'none', 'null']:
                        sold_cur.execute("UPDATE orders SET lot_number = ? WHERE order_id = ?", (lot_num, order_id))
                        updated_count += 1
                    else:
                        skipped_count += 1
                else:
                    skipped_count += 1
            else:
                skipped_count += 1
        
        sold_conn.commit()
        
        print(f"\n✓ Enrichment complete!")
        print(f"  - Updated: {updated_count} orders")
        print(f"  - Skipped (no LOT # found): {skipped_count} orders")
        
        # Show some sample data
        print("\nSample enriched orders:")
        sold_cur.execute("SELECT order_id, barcode, lot_number FROM orders WHERE lot_number IS NOT NULL LIMIT 5")
        samples = sold_cur.fetchall()
        for sample in samples:
            print(f"  Order: {sample[0]}, UPC: {sample[1]}, LOT #: {sample[2]}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        sold_conn.close()
        rawbol_conn.close()

if __name__ == "__main__":
    enrich_sold_lot_numbers()
