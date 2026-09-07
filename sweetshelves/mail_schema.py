"""Mail schema for Sweet Shelves."""

import sqlite3


def _normalize_mail_store(value):
    """Normalize user-supplied store values to canonical names."""
    raw = (value or '').strip().lower()
    if raw in ('ebay', 'ebaystore', 'ebay store', 'e-bay'):
        return 'eBay'
    if raw in ('amazon', 'amazonstore', 'amazon store'):
        return 'Amazon'
    return None


def _ensure_storemail_tables():
    """Create storemail.db tables for the centralized store message center."""
    try:
        conn = sqlite3.connect('storemail.db')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS store_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store TEXT NOT NULL,
                direction TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unread',
                sender_name TEXT,
                recipient_name TEXT,
                subject TEXT,
                body TEXT NOT NULL,
                reply_to_id INTEGER,
                external_source TEXT,
                external_id TEXT,
                external_payload TEXT,
                last_synced_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                sent_at TEXT,
                read_at TEXT
            )
        ''')
        # Forward-compatible schema upgrades for existing installs.
        for col_def in (
            'external_source TEXT',
            'external_id TEXT',
            'external_payload TEXT',
            'last_synced_at TEXT',
        ):
            col = col_def.split()[0]
            try:
                cur.execute(f'ALTER TABLE store_messages ADD COLUMN {col_def}')
            except Exception:
                pass
        cur.execute('CREATE INDEX IF NOT EXISTS idx_store_messages_created ON store_messages(created_at DESC)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_store_messages_store ON store_messages(store)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_store_messages_direction ON store_messages(direction)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_store_messages_status ON store_messages(status)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_store_messages_external ON store_messages(external_source, external_id)')
        cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_store_messages_external_unique ON store_messages(external_source, external_id)')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error initializing storemail.db: {e}")
