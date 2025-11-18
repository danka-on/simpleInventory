"""
Marketplace sales manager - handles marketplace.db operations
"""
import sqlite3
import datetime

def ensure_marketplace_db():
    """Create marketplace.db and marketplace_sales table if not exists."""
    conn = sqlite3.connect('marketplace.db')
    cur = conn.cursor()
    
    cur.execute('''CREATE TABLE IF NOT EXISTS marketplace_sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        barcode TEXT NOT NULL,
        title TEXT,
        quantity INTEGER DEFAULT 1,
        price REAL,
        sale_date TEXT,
        created_at TEXT
    )''')
    
    conn.commit()
    conn.close()

def add_marketplace_sale(barcode, title, quantity, price):
    """Add a marketplace sale entry."""
    try:
        ensure_marketplace_db()
        conn = sqlite3.connect('marketplace.db')
        cur = conn.cursor()
        
        sale_date = datetime.datetime.now().strftime('%Y-%m-%d')
        created_at = datetime.datetime.utcnow().isoformat()
        
        cur.execute('''INSERT INTO marketplace_sales 
            (barcode, title, quantity, price, sale_date, created_at)
            VALUES (?, ?, ?, ?, ?, ?)''',
            (barcode, title, quantity, price, sale_date, created_at))
        
        sale_id = cur.lastrowid
        conn.commit()
        conn.close()
        
        return {'success': True, 'id': sale_id}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def get_marketplace_sales(limit=None, offset=None):
    """Get marketplace sales with optional pagination."""
    try:
        ensure_marketplace_db()
        conn = sqlite3.connect('marketplace.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Get total count
        cur.execute('SELECT COUNT(*) FROM marketplace_sales')
        total = cur.fetchone()[0]
        
        # Get sales
        query = 'SELECT * FROM marketplace_sales ORDER BY created_at DESC'
        params = []
        
        if limit is not None:
            query += ' LIMIT ?'
            params.append(limit)
            if offset is not None:
                query += ' OFFSET ?'
                params.append(offset)
        
        cur.execute(query, params)
        sales = [dict(r) for r in cur.fetchall()]
        
        # Calculate totals (price is already total, not per-unit)
        cur.execute('SELECT SUM(quantity) as total_quantity, SUM(price) as total_revenue FROM marketplace_sales')
        totals_row = cur.fetchone()
        total_quantity = totals_row['total_quantity'] or 0
        total_revenue = totals_row['total_revenue'] or 0.0
        
        conn.close()
        
        return {
            'success': True,
            'sales': sales,
            'total': total,
            'total_quantity': total_quantity,
            'total_revenue': total_revenue
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def delete_marketplace_sale(sale_id):
    """Delete a marketplace sale from both marketplace.db and sold.db."""
    try:
        ensure_marketplace_db()
        
        # First, get sale details before deleting
        conn = sqlite3.connect('marketplace.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        cur.execute('SELECT * FROM marketplace_sales WHERE id = ?', (sale_id,))
        sale = cur.fetchone()
        
        if not sale:
            conn.close()
            return {'success': False, 'error': 'Sale not found'}
        
        sale_barcode = sale['barcode']
        sale_quantity = sale['quantity']
        sale_date = sale['sale_date']
        
        # Delete from marketplace.db
        cur.execute('DELETE FROM marketplace_sales WHERE id = ?', (sale_id,))
        deleted_marketplace = cur.rowcount
        
        conn.commit()
        conn.close()
        
        # Delete from sold.db (orders table where store='marketplace')
        # Match by barcode and approximate date (same day)
        sold_conn = sqlite3.connect('sold.db')
        sold_cur = sold_conn.cursor()
        
        # Find matching orders from the same day
        sold_cur.execute('''
            DELETE FROM orders 
            WHERE barcode = ? 
            AND store = 'marketplace'
            AND DATE(paid_time) = ?
            AND id IN (
                SELECT id FROM orders 
                WHERE barcode = ? 
                AND store = 'marketplace'
                AND DATE(paid_time) = ?
                ORDER BY paid_time DESC
                LIMIT ?
            )
        ''', (sale_barcode, sale_date, sale_barcode, sale_date, sale_quantity))
        
        deleted_sold = sold_cur.rowcount
        
        # Also check if this was marked as a resold return and revert it
        sold_cur.execute('''
            SELECT id FROM returns 
            WHERE resold = 1 
            AND resold_order_id = ?
        ''', (f'marketplace-{sale_id}',))
        
        return_row = sold_cur.fetchone()
        if return_row:
            return_id = return_row[0]
            
            # Revert the resold status
            sold_cur.execute('''
                UPDATE returns 
                SET resold = 0, resold_date = NULL, resold_order_id = NULL,
                    lifecycle_count = lifecycle_count - 1
                WHERE id = ?
            ''', (return_id,))
            
            # Delete the lifecycle event
            sold_cur.execute('''
                DELETE FROM return_lifecycle_events 
                WHERE return_id = ? 
                AND event_type = 'resold' 
                AND order_id = ?
            ''', (return_id, f'marketplace-{sale_id}'))
            
            print(f'[DELETE] Reverted resold status for return #{return_id}')
        
        sold_conn.commit()
        sold_conn.close()
        
        return {
            'success': True, 
            'deleted': deleted_marketplace,
            'deleted_from_sold': deleted_sold
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {'success': False, 'error': str(e)}

def update_marketplace_sale(sale_id, barcode=None, title=None, quantity=None, price=None):
    """Update a marketplace sale."""
    try:
        ensure_marketplace_db()
        conn = sqlite3.connect('marketplace.db')
        cur = conn.cursor()
        
        updates = []
        params = []
        
        if barcode is not None:
            updates.append('barcode = ?')
            params.append(barcode)
        if title is not None:
            updates.append('title = ?')
            params.append(title)
        if quantity is not None:
            updates.append('quantity = ?')
            params.append(quantity)
        if price is not None:
            updates.append('price = ?')
            params.append(price)
        
        if not updates:
            return {'success': False, 'error': 'No fields to update'}
        
        params.append(sale_id)
        query = f'UPDATE marketplace_sales SET {", ".join(updates)} WHERE id = ?'
        
        cur.execute(query, params)
        updated = cur.rowcount
        
        conn.commit()
        conn.close()
        
        return {'success': True, 'updated': updated}
    except Exception as e:
        return {'success': False, 'error': str(e)}
