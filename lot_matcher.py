"""
Smart LOT matching logic for sold items.
Matches sold orders to LOTs based on:
1. UPC match
2. Latest LOT first (by import_date DESC)
3. aftersale_quantity tracking - fill up one LOT before moving to next
4. Unmatched items assigned to "lostlot"
"""
import sqlite3
from datetime import datetime
from DBmanager import connect_db

def match_sold_item_to_lot(barcode, sold_date):
    """
    Find the best LOT match for a sold item.
    
    Args:
        barcode: UPC/barcode of the sold item
        sold_date: Date when item was sold (ISO format string) - not used in matching anymore
    
    Returns:
        lot_number (str) - returns "lostlot" if no match found
    """
    try:
        with connect_db('rawbol.db') as rawbol_conn:
            rawbol_conn.row_factory = sqlite3.Row
            rawbol_cur = rawbol_conn.cursor()

            # Acquire write lock before SELECT to prevent concurrent decrements
            rawbol_cur.execute('BEGIN IMMEDIATE')

            # Normalize barcode - strip leading zeros for matching
            # sold.db may have "088235725301" while rawbol.db has "88235725301"
            barcode_normalized = barcode.lstrip('0') if barcode else barcode

            # Get all LOTs with this UPC, ordered by import_date DESC (newest first)
            # Match both original and normalized barcode to handle leading zero variations
            rawbol_cur.execute('''
                SELECT
                    lot_number,
                    import_date,
                    upc,
                    SUM(aftersale_quantity) as available_quantity
                FROM raw_bol_items
                WHERE (upc = ? COLLATE NOCASE OR upc = ? COLLATE NOCASE)
                AND import_date IS NOT NULL
                AND import_date != ''
                GROUP BY lot_number, import_date, upc
                HAVING available_quantity > 0
                ORDER BY import_date DESC
            ''', (barcode, barcode_normalized))

            available_lots = rawbol_cur.fetchall()

            if not available_lots:
                return "lostlot"

            # Use the first LOT with available quantity (newest LOT)
            selected_lot = available_lots[0]
            lot_number = selected_lot['lot_number']
            upc = selected_lot['upc']

            # Decrement aftersale_quantity for this specific UPC in this LOT
            rawbol_cur.execute('''
                UPDATE raw_bol_items
                SET aftersale_quantity = aftersale_quantity - 1
                WHERE lot_number = ?
                AND upc = ?
                AND aftersale_quantity > 0
            ''', (lot_number, upc))

            return lot_number

    except Exception as e:
        print(f"Error matching LOT for barcode {barcode}: {e}")
        return "lostlot"


def enrich_sold_order_with_lot(order_id, barcode, sold_date):
    """
    Update a single sold order with matched LOT number.
    
    Args:
        order_id: ID of the order in sold.db
        barcode: UPC/barcode
        sold_date: Date sold
    
    Returns:
        dict with success status and lot_number
    """
    try:
        lot_number = match_sold_item_to_lot(barcode, sold_date)

        with connect_db('sold.db') as sold_conn:
            sold_cur = sold_conn.cursor()
            sold_cur.execute('''
                UPDATE orders
                SET lot_number = ?
                WHERE id = ?
            ''', (lot_number, order_id))

        return {'success': True, 'lot_number': lot_number}

    except Exception as e:
        return {'success': False, 'error': str(e)}


def reset_aftersale_quantities():
    """
    Reset aftersale_quantity to match quantity for all items in raw_bol_items.
    Called before backfill to start fresh.
    """
    try:
        with connect_db('rawbol.db') as rawbol_conn:
            rawbol_cur = rawbol_conn.cursor()
            rawbol_cur.execute('''
                UPDATE raw_bol_items
                SET aftersale_quantity = quantity
            ''')
            rows_updated = rawbol_cur.rowcount
        
        print(f"Reset {rows_updated} items: aftersale_quantity = quantity")
        return {'success': True, 'rows_updated': rows_updated}
        
    except Exception as e:
        print(f"Error resetting aftersale_quantities: {e}")
        return {'success': False, 'error': str(e)}


def backfill_all_sold_orders():
    """
    Backfill LOT numbers for all existing sold orders.
    1. Resets all lot_number assignments in sold.db
    2. Resets aftersale_quantity in rawbol.db
    3. Processes orders chronologically (oldest first) to properly track quantity usage.
    
    Returns:
        dict with statistics
    """
    try:
        # Step 1: Reset all lot_number assignments
        print("Step 1: Resetting all existing lot_number assignments...")
        with connect_db('sold.db') as sold_conn:
            sold_cur = sold_conn.cursor()
            sold_cur.execute('''
                UPDATE orders
                SET lot_number = NULL
                WHERE paid_time IS NOT NULL
            ''')
            reset_count = sold_cur.rowcount
            print(f"  Reset {reset_count} lot_number assignments")

            # Step 2: Reset aftersale_quantities
            print("\nStep 2: Resetting aftersale_quantities in rawbol.db...")
            reset_result = reset_aftersale_quantities()
            if not reset_result['success']:
                return reset_result

            # Step 3: Get all sold orders ordered by paid_time ASC
            print("\nStep 3: Processing orders chronologically...")
            sold_conn.row_factory = sqlite3.Row
            sold_cur = sold_conn.cursor()
            sold_cur.execute('''
                SELECT id, barcode, paid_time
                FROM orders
                WHERE paid_time IS NOT NULL
                AND barcode IS NOT NULL
                AND barcode != ''
                ORDER BY paid_time ASC
            ''')
            orders = sold_cur.fetchall()
        
        total_orders = len(orders)
        matched_count = 0
        lostlot_count = 0
        error_count = 0
        
        print(f"Starting backfill for {total_orders} orders...")
        
        for i, order in enumerate(orders):
            order_id = order['id']
            barcode = order['barcode']
            paid_time = order['paid_time']
            
            result = enrich_sold_order_with_lot(order_id, barcode, paid_time)
            
            if result['success']:
                if result.get('lot_number') == 'lostlot':
                    lostlot_count += 1
                else:
                    matched_count += 1
            else:
                error_count += 1
                print(f"Error enriching order {order_id}: {result.get('error')}")
            
            # Progress update every 50 orders
            if (i + 1) % 50 == 0:
                print(f"Progress: {i + 1}/{total_orders} orders processed...")
        
        print(f"\nBackfill complete!")
        print(f"  - Matched to LOTs: {matched_count}")
        print(f"  - Assigned to lostlot: {lostlot_count}")
        print(f"  - Errors: {error_count}")
        
        return {
            'success': True,
            'total_orders': total_orders,
            'matched': matched_count,
            'lostlot': lostlot_count,
            'errors': error_count
        }
        
    except Exception as e:
        return {'success': False, 'error': str(e)}


def enrich_new_orders(days_back=7):
    """
    Enrich recently added orders that don't have LOT numbers yet.
    Useful for auto-enriching during sync operations.
    
    Args:
        days_back: Number of days to look back (default 7)
    
    Returns:
        dict with statistics
    """
    try:
        with connect_db('sold.db') as sold_conn:
            sold_conn.row_factory = sqlite3.Row
            sold_cur = sold_conn.cursor()
            sold_cur.execute('''
                SELECT id, barcode, paid_time
                FROM orders
                WHERE paid_time IS NOT NULL
                AND barcode IS NOT NULL
                AND barcode != ''
                AND (lot_number IS NULL OR lot_number = '')
                AND paid_time >= datetime('now', ? || ' days')
                ORDER BY paid_time ASC
            ''', (f'-{days_back}',))
            orders = sold_cur.fetchall()
        
        matched_count = 0
        lostlot_count = 0
        
        for order in orders:
            result = enrich_sold_order_with_lot(order['id'], order['barcode'], order['paid_time'])
            if result['success']:
                if result.get('lot_number') == 'lostlot':
                    lostlot_count += 1
                else:
                    matched_count += 1
        
        return {
            'success': True,
            'total_checked': len(orders),
            'matched': matched_count,
            'lostlot': lostlot_count
        }
        
    except Exception as e:
        return {'success': False, 'error': str(e)}
