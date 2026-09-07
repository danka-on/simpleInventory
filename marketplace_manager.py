"""
Marketplace sales manager - handles marketplace.db operations
"""
import sqlite3
import datetime
from pathlib import Path
from contextlib import closing

BASE_DIR = Path(__file__).resolve().parent


def _serialize_pickup_row(row):
    return {
        'id': row['id'],
        'barcode': row['barcode'] or '',
        'title': row['title'] or '',
        'image_url': row['image_url'] or '',
        'pickup_date': row['pickup_date'] or '',
        'pickup_time': row['pickup_time'] or '',
        'scheduled_at': row['scheduled_at'] or '',
        'source': row['source'] or 'manual',
        'notes': row['notes'] or '',
        'status': row['status'] or 'scheduled',
        'created_at': row['created_at'] or '',
    }

def ensure_marketplace_db():
    """Create marketplace.db tables used by marketplace sale flows."""
    with closing(sqlite3.connect(str(BASE_DIR / 'marketplace.db'), timeout=30)) as conn:
        cur = conn.cursor()
    
        cur.execute('''CREATE TABLE IF NOT EXISTS marketplace_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode TEXT NOT NULL,
            title TEXT,
            quantity INTEGER DEFAULT 1,
            price REAL,
            sale_date TEXT,
            created_at TEXT,
            session_id TEXT
        )''')

        # Migration: add session_id if missing
        try:
            cur.execute("SELECT session_id FROM marketplace_sales LIMIT 1")
        except sqlite3.OperationalError:
            cur.execute("ALTER TABLE marketplace_sales ADD COLUMN session_id TEXT")

        # Migration: add price_auto flag (1 = auto-split from session total, 0 = manually entered)
        try:
            cur.execute("SELECT price_auto FROM marketplace_sales LIMIT 1")
        except sqlite3.OperationalError:
            cur.execute("ALTER TABLE marketplace_sales ADD COLUMN price_auto INTEGER DEFAULT 0")

        cur.execute('''CREATE TABLE IF NOT EXISTS marketplace_pickups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode TEXT,
            title TEXT NOT NULL,
            image_url TEXT,
            pickup_date TEXT NOT NULL,
            pickup_time TEXT NOT NULL,
            scheduled_at TEXT NOT NULL,
            source TEXT DEFAULT 'manual',
            notes TEXT,
            status TEXT DEFAULT 'scheduled',
            created_at TEXT
        )''')

        cur.execute('CREATE INDEX IF NOT EXISTS idx_marketplace_pickups_status ON marketplace_pickups(status)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_marketplace_pickups_scheduled ON marketplace_pickups(scheduled_at)')

        conn.commit()
        conn.close()


def add_marketplace_pickup(title, pickup_date, pickup_time, barcode=None, image_url=None, source='manual', notes=None):
    """Create a scheduled pickup entry."""
    conn = None
    try:
        ensure_marketplace_db()
        title_clean = str(title or '').strip()
        barcode_clean = str(barcode or '').strip()
        if not title_clean and not barcode_clean:
            return {'success': False, 'error': 'Missing item title or barcode'}

        scheduled_dt = datetime.datetime.strptime(
            f'{pickup_date} {pickup_time}',
            '%Y-%m-%d %H:%M'
        )

        created_at = datetime.datetime.now().isoformat(timespec='seconds')
        scheduled_at = scheduled_dt.isoformat(timespec='minutes')

        conn = sqlite3.connect(str(BASE_DIR / 'marketplace.db'), timeout=30)
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO marketplace_pickups
            (barcode, title, image_url, pickup_date, pickup_time, scheduled_at, source, notes, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?)
        ''', (
            barcode_clean or None,
            title_clean or barcode_clean,
            str(image_url or '').strip() or None,
            pickup_date,
            pickup_time,
            scheduled_at,
            str(source or 'manual').strip() or 'manual',
            str(notes or '').strip() or None,
            created_at
        ))

        pickup_id = cur.lastrowid
        conn.commit()
        conn.close()

        return {
            'success': True,
            'pickup': {
                'id': pickup_id,
                'barcode': barcode_clean,
                'title': title_clean or barcode_clean,
                'image_url': str(image_url or '').strip(),
                'pickup_date': pickup_date,
                'pickup_time': pickup_time,
                'scheduled_at': scheduled_at,
                'source': str(source or 'manual').strip() or 'manual',
                'notes': str(notes or '').strip(),
                'status': 'scheduled',
                'created_at': created_at,
            }
        }
    except ValueError:
        return {'success': False, 'error': 'Invalid pickup date or time'}
    except Exception as e:
        return {'success': False, 'error': str(e)}
    finally:
        if conn is not None:
            conn.close()


def get_marketplace_pickups(status='scheduled', limit=None):
    """Return scheduled marketplace pickups ordered by soonest first."""
    conn = None
    try:
        ensure_marketplace_db()
        conn = sqlite3.connect(str(BASE_DIR / 'marketplace.db'), timeout=30)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        query = 'SELECT * FROM marketplace_pickups'
        params = []
        if status:
            query += ' WHERE status = ?'
            params.append(status)
        query += ' ORDER BY scheduled_at ASC, created_at ASC'
        if limit is not None:
            query += ' LIMIT ?'
            params.append(limit)

        cur.execute(query, params)
        pickups = [_serialize_pickup_row(row) for row in cur.fetchall()]
        conn.close()

        return {
            'success': True,
            'pickups': pickups,
            'total': len(pickups)
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}
    finally:
        if conn is not None:
            conn.close()

def add_marketplace_sale(barcode, title, quantity, price, session_id=None, price_auto=False):
    """Add a marketplace sale entry."""
    conn = None
    try:
        ensure_marketplace_db()
        conn = sqlite3.connect(str(BASE_DIR / 'marketplace.db'), timeout=30)
        cur = conn.cursor()

        sale_date = datetime.datetime.now().strftime('%Y-%m-%d')
        created_at = datetime.datetime.utcnow().isoformat()

        cur.execute('''INSERT INTO marketplace_sales
            (barcode, title, quantity, price, sale_date, created_at, session_id, price_auto)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (barcode, title, quantity, price, sale_date, created_at, session_id, 1 if price_auto else 0))

        sale_id = cur.lastrowid
        conn.commit()
        conn.close()

        return {'success': True, 'id': sale_id}
    except Exception as e:
        return {'success': False, 'error': str(e)}
    finally:
        if conn is not None:
            conn.close()

def get_marketplace_sales(limit=None, offset=None):
    """Get marketplace sales with optional pagination."""
    conn = None
    try:
        ensure_marketplace_db()
        conn = sqlite3.connect(str(BASE_DIR / 'marketplace.db'), timeout=30)
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
    finally:
        if conn is not None:
            conn.close()

def delete_marketplace_sale(sale_id):
    """Delete one sale and its corresponding order in one transaction.

    Old sales have no order reference. Only remove an unambiguously matching
    legacy order; a quantity is a unit count, never a number of order rows.
    """
    try:
        ensure_marketplace_db()
        with closing(sqlite3.connect(str(BASE_DIR / 'marketplace.db'), timeout=30)) as conn:
            conn.row_factory = sqlite3.Row
            has_sold = (BASE_DIR / 'sold.db').is_file()
            if has_sold:
                conn.execute('ATTACH DATABASE ? AS sold', (str(BASE_DIR / 'sold.db'),))
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                sale = conn.execute('SELECT * FROM marketplace_sales WHERE id = ?', (sale_id,)).fetchone()
                if sale is None:
                    return {'success': False, 'error': 'Sale not found'}
                references = (f'marketplace-{sale_id}', f'marketplace-sale-bulk-{sale_id}')
                tables = {row[0] for row in conn.execute("SELECT name FROM sold.sqlite_master WHERE type='table'")} if has_sold else set()
                order_ids = []
                if 'orders' in tables:
                    orders = conn.execute("""
                        SELECT id FROM sold.orders WHERE order_id IN (?, ?)
                        AND store IN ('marketplace', 'marketplace-sale-bulk')
                    """, references).fetchall()
                    if not orders:
                        orders = conn.execute("""
                            SELECT id FROM sold.orders WHERE barcode = ?
                            AND store IN ('marketplace', 'marketplace-sale-bulk')
                            AND COALESCE(order_id, '') = '' AND DATE(paid_time) = ?
                            AND quantity = ? AND COALESCE(title, '') = ?
                            AND COALESCE(price, 0) = ?
                        """, (sale['barcode'], sale['sale_date'], sale['quantity'],
                              sale['title'] or '', sale['price'] or 0)).fetchall()
                    if len(orders) > 1:
                        return {'success': False, 'error': 'Multiple sold orders match this legacy sale. Resolve the order link before deleting.'}
                    order_ids = [row['id'] for row in orders]
                    for order_id in order_ids:
                        for child in ('order_removal_allocations', 'ready_to_ship_notes', 'ready_to_ship_order_labels'):
                            if child in tables:
                                conn.execute(f'DELETE FROM sold.{child} WHERE order_row_id = ?', (order_id,))
                        conn.execute('DELETE FROM sold.orders WHERE id = ?', (order_id,))
                if 'returns' in tables:
                    return_rows = conn.execute('SELECT id FROM sold.returns WHERE resold = 1 AND resold_order_id IN (?, ?)', references).fetchall()
                    for row in return_rows:
                        conn.execute("""UPDATE sold.returns SET resold = 0, resold_date = NULL,
                            resold_order_id = NULL, lifecycle_count = MAX(0, COALESCE(lifecycle_count, 0) - 1)
                            WHERE id = ?""", (row['id'],))
                        if 'return_lifecycle_events' in tables:
                            conn.execute("""DELETE FROM sold.return_lifecycle_events WHERE return_id = ?
                                AND event_type = 'resold' AND order_id IN (?, ?)""", (row['id'], *references))
                deleted = conn.execute('DELETE FROM marketplace_sales WHERE id = ?', (sale_id,)).rowcount
            return {'success': True, 'deleted': deleted, 'deleted_from_sold': len(order_ids)}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def update_marketplace_sale(sale_id, barcode=None, title=None, quantity=None, price=None):
    """Update a marketplace sale."""
    conn = None
    try:
        ensure_marketplace_db()
        conn = sqlite3.connect(str(BASE_DIR / 'marketplace.db'), timeout=30)
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
    finally:
        if conn is not None:
            conn.close()
