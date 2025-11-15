"""
Smart LOT matching logic for sold items.
Matches sold orders to LOTs based on:
1. UPC match
2. Closest LOT import_date BEFORE sold date
3. Quantity tracking - fill up one LOT before moving to next older LOT
"""
import sqlite3
from datetime import datetime

def match_sold_item_to_lot(barcode, sold_date):
    """
    Find the best LOT match for a sold item.
    
    Args:
        barcode: UPC/barcode of the sold item
        sold_date: Date when item was sold (ISO format string)
    
    Returns:
        lot_number (str) or None if no match found
    """
    try:
        rawbol_conn = sqlite3.connect('rawbol.db')
        rawbol_conn.row_factory = sqlite3.Row
        rawbol_cur = rawbol_conn.cursor()
        
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        
        # Parse sold date
        sold_datetime = datetime.fromisoformat(sold_date.replace('Z', '+00:00'))
        
        # Normalize barcode - strip leading zeros for matching
        # sold.db may have "088235725301" while rawbol.db has "88235725301"
        barcode_normalized = barcode.lstrip('0') if barcode else barcode
        
        # Get all LOTs with this UPC, ordered by import_date DESC (newest first)
        # Only consider LOTs with import_date BEFORE sold date
        # Match both original and normalized barcode to handle leading zero variations
        rawbol_cur.execute('''
            SELECT 
                lot_number,
                import_date,
                SUM(quantity) as total_quantity
            FROM raw_bol_items
            WHERE (upc = ? COLLATE NOCASE OR upc = ? COLLATE NOCASE)
            AND import_date IS NOT NULL
            AND import_date != ''
            GROUP BY lot_number, import_date
            ORDER BY import_date DESC
        ''', (barcode, barcode_normalized))
        
        available_lots = []
        for row in rawbol_cur.fetchall():
            try:
                lot_date = datetime.fromisoformat(row['import_date'])
                # Make lot_date timezone-aware to match sold_datetime
                if lot_date.tzinfo is None:
                    # Assume UTC if no timezone
                    from datetime import timezone
                    lot_date = lot_date.replace(tzinfo=timezone.utc)
                
                # Only include LOTs before sold date
                if lot_date <= sold_datetime:
                    available_lots.append({
                        'lot_number': row['lot_number'],
                        'import_date': row['import_date'],
                        'lot_datetime': lot_date,
                        'total_quantity': row['total_quantity']
                    })
            except (ValueError, TypeError):
                # Skip LOTs with invalid dates
                continue
        
        if not available_lots:
            rawbol_conn.close()
            sold_conn.close()
            return None
        
        # Sort by date DESC (closest to sold date first)
        available_lots.sort(key=lambda x: x['lot_datetime'], reverse=True)
        
        # Check each LOT starting from closest, considering quantity usage
        for lot in available_lots:
            lot_number = lot['lot_number']
            total_quantity = lot['total_quantity']
            
            # Count how many items from this LOT have already been sold
            # Also normalize barcode for consistency
            sold_cur.execute('''
                SELECT COUNT(*) as sold_count
                FROM orders
                WHERE (barcode = ? COLLATE NOCASE OR barcode = ? COLLATE NOCASE)
                AND lot_number = ?
                AND paid_time IS NOT NULL
            ''', (barcode, barcode_normalized, lot_number))
            
            sold_count = sold_cur.fetchone()['sold_count']
            
            # If this LOT still has available quantity, use it
            if sold_count < total_quantity:
                rawbol_conn.close()
                sold_conn.close()
                return lot_number
        
        # All LOTs are filled up, return None
        rawbol_conn.close()
        sold_conn.close()
        return None
        
    except Exception as e:
        print(f"Error matching LOT for barcode {barcode}: {e}")
        return None


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
        
        if lot_number:
            sold_conn = sqlite3.connect('sold.db')
            sold_cur = sold_conn.cursor()
            
            sold_cur.execute('''
                UPDATE orders
                SET lot_number = ?
                WHERE id = ?
            ''', (lot_number, order_id))
            
            sold_conn.commit()
            sold_conn.close()
            
            return {'success': True, 'lot_number': lot_number}
        else:
            return {'success': True, 'lot_number': None, 'message': 'No matching LOT found'}
            
    except Exception as e:
        return {'success': False, 'error': str(e)}


def backfill_all_sold_orders():
    """
    Backfill LOT numbers for all existing sold orders.
    Processes orders chronologically (oldest first) to properly track quantity usage.
    
    Returns:
        dict with statistics
    """
    try:
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        
        # Get all sold orders ordered by paid_time ASC (oldest first for proper quantity tracking)
        sold_cur.execute('''
            SELECT id, barcode, paid_time
            FROM orders
            WHERE paid_time IS NOT NULL
            AND barcode IS NOT NULL
            AND barcode != ''
            ORDER BY paid_time ASC
        ''')
        
        orders = sold_cur.fetchall()
        sold_conn.close()
        
        total_orders = len(orders)
        enriched_count = 0
        no_match_count = 0
        error_count = 0
        
        print(f"Starting backfill for {total_orders} orders...")
        
        for i, order in enumerate(orders):
            order_id = order['id']
            barcode = order['barcode']
            paid_time = order['paid_time']
            
            result = enrich_sold_order_with_lot(order_id, barcode, paid_time)
            
            if result['success']:
                if result.get('lot_number'):
                    enriched_count += 1
                else:
                    no_match_count += 1
            else:
                error_count += 1
                print(f"Error enriching order {order_id}: {result.get('error')}")
            
            # Progress update every 50 orders
            if (i + 1) % 50 == 0:
                print(f"Progress: {i + 1}/{total_orders} orders processed...")
        
        print(f"Backfill complete!")
        print(f"  - Enriched: {enriched_count}")
        print(f"  - No match: {no_match_count}")
        print(f"  - Errors: {error_count}")
        
        return {
            'success': True,
            'total_orders': total_orders,
            'enriched': enriched_count,
            'no_match': no_match_count,
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
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        
        # Get recent orders without lot_number
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
        sold_conn.close()
        
        enriched_count = 0
        no_match_count = 0
        
        for order in orders:
            result = enrich_sold_order_with_lot(order['id'], order['barcode'], order['paid_time'])
            if result['success'] and result.get('lot_number'):
                enriched_count += 1
            else:
                no_match_count += 1
        
        return {
            'success': True,
            'total_checked': len(orders),
            'enriched': enriched_count,
            'no_match': no_match_count
        }
        
    except Exception as e:
        return {'success': False, 'error': str(e)}
