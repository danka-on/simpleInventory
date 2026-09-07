"""Confirmed-name evidence, stored transactionally beside each source match."""
import json
import uuid

DATABASES = {'order': 'sold.db', 'listing': 'listing_alerts.db'}


def barcode_key(value):
    raw = str(value or '').strip().casefold()
    base, separator, suffix = raw.partition('-')
    if base.endswith('.0') and base[:-2].isdigit():
        base = base[:-2]
    if base.isdigit():
        base = base.lstrip('0') or '0'
    return base + separator + suffix


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS finder_aliases (
        id TEXT PRIMARY KEY, source_key TEXT NOT NULL UNIQUE,
        barcode_key TEXT NOT NULL, title TEXT NOT NULL
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS finder_alias_changes (
        token TEXT PRIMARY KEY, source_key TEXT NOT NULL,
        before_json TEXT, after_id TEXT
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_finder_alias_barcode ON finder_aliases(barcode_key)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_finder_alias_change_source ON finder_alias_changes(source_key)')


def update_alias(conn, source_key, barcode='', title='', undo_token=None):
    """Called inside the match transaction. Undo only this source's last evidence.

    A new id per confirmation prevents undo from overwriting subsequent learning
    or resurrecting an alias that the user explicitly forgot.
    """
    ensure_schema(conn)
    current = conn.execute(
        'SELECT id, source_key, barcode_key, title FROM finder_aliases WHERE source_key = ?',
        (source_key,)
    ).fetchone()
    current = list(current) if current else None
    if undo_token:
        change = conn.execute(
            'SELECT before_json, after_id FROM finder_alias_changes WHERE token = ? AND source_key = ?',
            (str(undo_token), source_key)
        ).fetchone()
        if change:
            if (current[0] if current else None) == change[1]:
                conn.execute('DELETE FROM finder_aliases WHERE source_key = ?', (source_key,))
                before = json.loads(change[0]) if change[0] else None
                if before:
                    conn.execute('INSERT INTO finder_aliases VALUES (?, ?, ?, ?)', before)
            conn.execute('DELETE FROM finder_alias_changes WHERE token = ?', (str(undo_token),))
        return None

    title = ' '.join(str(title or '').split())[:2000]
    key = barcode_key(barcode)
    conn.execute('DELETE FROM finder_aliases WHERE source_key = ?', (source_key,))
    new_id = uuid.uuid4().hex if title and key else None
    if new_id:
        conn.execute('INSERT INTO finder_aliases VALUES (?, ?, ?, ?)',
                     (new_id, source_key, key, title))
    token = uuid.uuid4().hex
    # Only the latest match can be undone for a source; keep storage bounded.
    conn.execute('DELETE FROM finder_alias_changes WHERE source_key = ?', (source_key,))
    conn.execute('INSERT INTO finder_alias_changes VALUES (?, ?, ?, ?)',
                 (token, source_key, json.dumps(current) if current else None, new_id))
    return token


def load_aliases(connect_db):
    lookup = {}
    for source, database in DATABASES.items():
        with connect_db(database) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='finder_aliases'").fetchone():
                continue
            for alias_id, key, title in conn.execute('SELECT id, barcode_key, title FROM finder_aliases'):
                lookup.setdefault(key, []).append({'id': alias_id, 'source': source, 'title': title})
    return lookup


def forget_alias(conn, alias_id):
    ensure_schema(conn)
    row = conn.execute('SELECT barcode_key, title FROM finder_aliases WHERE id = ?', (alias_id,)).fetchone()
    if row:
        # One visible title can have evidence from several orders. Forget all
        # copies in this database; the route also removes copies in the other.
        forget_name(conn, *row)
    return tuple(row) if row else None


def forget_name(conn, key, title):
    ensure_schema(conn)
    conn.execute('DELETE FROM finder_aliases WHERE barcode_key = ? AND title = ?', (key, title))
    for token, before_json in conn.execute('SELECT token, before_json FROM finder_alias_changes WHERE before_json IS NOT NULL').fetchall():
        before = json.loads(before_json)
        if before[2:] == [key, title]:
            conn.execute('UPDATE finder_alias_changes SET before_json = NULL WHERE token = ?', (token,))
