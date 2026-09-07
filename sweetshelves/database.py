"""Database for Sweet Shelves."""

import os
import sqlite3
from contextlib import contextmanager
from . import config as ss_config


def get_db_connection(db_name):
    """Get a request-scoped database connection (reused within same request, closed at end)"""
    from flask import g
    if not hasattr(g, '_db_connections'):
        g._db_connections = {}

    if db_name not in g._db_connections:
        if os.path.isabs(db_name):
            db_path = db_name
        else:
            db_path = str(ss_config.BASE_DIR / db_name)

        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        g._db_connections[db_name] = conn

    return g._db_connections[db_name]


@contextmanager
def db_connection(db_name, row_factory=True):
    """Context manager for database operations with automatic commit/rollback"""
    conn = get_db_connection(db_name)
    if not row_factory:
        original_factory = conn.row_factory
        conn.row_factory = None
    
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if not row_factory:
            conn.row_factory = original_factory


def enable_wal_mode():
    """Enable Write-Ahead Logging for all SQLite databases"""
    databases = ['sold.db', 'bol.db', 'searchRack.db', 'ebayStore.db', 'amazonStore.db', 'rawbol.db', 'rackhistory.db', 'deleted.db', 'listing_alerts.db', 'fbstore.db', 'listagent.db', 'listinglog.db', 'preplog.db', 'pricemaster.db', 'storemail.db']
    for db_name in databases:
        try:
            db_path = ss_config.BASE_DIR / db_name
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                try:
                    conn.execute('PRAGMA journal_mode=WAL')
                finally:
                    conn.close()
        except Exception:
            pass  # Skip if database doesn't exist or error occurs


def create_database_indexes():
    """Create indexes on frequently searched columns for all databases"""
    print("🔍 Creating database indexes for faster searches...")
    
    # Index definitions: {database: [(table, column), ...]}
    index_configs = {
        'bol.db': [
            ('bol_items', 'upc'),
            ('bol_items', 'item_description'),
            ('bol_items', 'lot_number')
        ],
        'rawbol.db': [
            ('raw_bol_items', 'upc'),
            ('raw_bol_items', 'item_description'),
            ('raw_bol_items', 'lot_number')
        ],
        'searchRack.db': [
            ('SEARCHRACK', 'BARCODE'),
            ('SEARCHRACK', 'TITLE'),
            ('SEARCHRACK', 'item_position'),
            ('SEARCHRACK', 'CREATED_AT')
        ],
        'sold.db': [
            ('orders', 'barcode'),
            ('orders', 'sku'),
            ('orders', 'order_id'),
            ('orders', 'store'),
            ('returns', 'upc'),
            ('returns', 'order_id')
        ],
        'ebayStore.db': [
            ('INVENTORY', 'UPC'),
            ('INVENTORY', 'SKU'),
            ('orders', 'OrderID')
        ],
        'amazonStore.db': [
            ('INVENTORY', 'UPC'),
            ('INVENTORY', 'SKU'),
            ('INVENTORY', 'asin'),
            ('orders', 'AmazonOrderId')
        ]
    }

    # These expression indexes match the normalized barcode predicates used by
    # scanner endpoints. A normal BARCODE/UPC index cannot service
    # LOWER(TRIM(column)), which previously turned a missing scan into a full
    # table read.
    normalized_barcode_indexes = {
        'rawbol.db': [
            ('raw_bol_items', 'upc', 'idx_raw_bol_items_upc_normalized')
        ],
        'searchRack.db': [
            ('SEARCHRACK', 'BARCODE', 'idx_searchrack_barcode_normalized')
        ],
    }
    
    for db_name, indexes in index_configs.items():
        conn = None
        try:
            db_path = ss_config.BASE_DIR / db_name
            if not db_path.exists():
                continue
                
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            
            for table, column in indexes:
                try:
                    # Check if table exists
                    cur.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
                    if not cur.fetchone():
                        continue
                    
                    # Check if column exists
                    cur.execute(f"PRAGMA table_info({table})")
                    cols = [row[1].lower() for row in cur.fetchall()]
                    if column.lower() not in cols:
                        continue
                    
                    # Create index if it doesn't exist
                    index_name = f"idx_{table}_{column}".replace('-', '_')
                    cur.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table}({column} COLLATE NOCASE)")
                    print(f"  ✅ Index created: {db_name}.{table}.{column}")
                except Exception as e:
                    print(f"  ⚠️ Could not create index on {db_name}.{table}.{column}: {e}")

            for table, column, index_name in normalized_barcode_indexes.get(db_name, []):
                try:
                    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
                    if not cur.fetchone():
                        continue
                    cur.execute(f"PRAGMA table_info({table})")
                    cols = [row[1].lower() for row in cur.fetchall()]
                    if column.lower() not in cols:
                        continue
                    cur.execute(
                        f"CREATE INDEX IF NOT EXISTS {index_name} "
                        f"ON {table}(LOWER(TRIM({column})))"
                    )
                    print(f"  ✅ Normalized barcode index created: {db_name}.{table}.{column}")
                except Exception as e:
                    print(f"  ⚠️ Could not create normalized barcode index on {db_name}.{table}.{column}: {e}")
            
            conn.commit()
        except Exception as e:
            print(f"  ⚠️ Error processing {db_name}: {e}")
        finally:
            if conn is not None:
                conn.close()
    
    print("✅ Database indexes created")


def _ensure_bol_items_fast_indexes():
    """
    One-time index setup for /api/bol_items hot-path queries.
    Keeps the duplicate-row canonicalization and default list loads fast on large bol.db files.
    """
    global _bol_items_fast_indexes_ready
    if _bol_items_fast_indexes_ready:
        return
    conn = None
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'bol.db'))
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bol_items'")
        if not cur.fetchone():
            _bol_items_fast_indexes_ready = True
            return

        # Supports fast "latest row per UPC+LOT" lookup using MAX(id) + normalized lot expression.
        cur.execute('''
            CREATE INDEX IF NOT EXISTS idx_bol_items_upc_lotnorm_id
            ON bol_items(upc COLLATE NOCASE, COALESCE(lot_number, '') COLLATE NOCASE, id DESC)
        ''')
        # Helps default import-date ordering and pagination scans.
        cur.execute('''
            CREATE INDEX IF NOT EXISTS idx_bol_items_import_date_id
            ON bol_items(import_date DESC, id DESC)
        ''')
        conn.commit()
        _bol_items_fast_indexes_ready = True
    except Exception as e:
        print(f"Warning: failed to ensure bol_items fast indexes: {e}")
    finally:
        if conn is not None:
            conn.close()


_bol_items_fast_indexes_ready = False


def _sqlite_ident(name):
    return '"' + str(name).replace('"', '""') + '"'


def close_db_connections(exception):
    """Close all request-scoped database connections at end of request."""
    from flask import g
    connections = getattr(g, '_db_connections', {})
    for conn in connections.values():
        try:
            conn.close()
        except Exception:
            pass
    connections.clear()
