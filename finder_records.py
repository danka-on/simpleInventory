"""Read-only, paginated UPC records across the application's local data stores."""
import json
import re
import sqlite3


DATABASES = {
    'sold.db': 'Sold history and returns',
    'preplog.db': 'Prep history',
    'bol.db': 'Prep details and BOL',
    'rackhistory.db': 'Rack history',
    'searchRack.db': 'Warehouse and FBA prep',
    'rawbol.db': 'Imported BOL',
    'listinglog.db': 'Listing history',
    'listagent.db': 'Listing agent',
    'ebayStore.db': 'eBay',
    'amazonStore.db': 'Amazon',
    'fbstore.db': 'Facebook',
    'listing_alerts.db': 'Listing matches and alerts',
    'deleted.db': 'Deleted inventory',
    'rack.db': 'Rack scans',
    'pricemaster.db': 'Price history',
}
UPC_COLUMNS = {'upc', 'barcode', 'barcode_key', 'source_upc', 'base_upc', 'source_base_upc', 'item_barcode', 'product_upc'}
BASE_COLUMNS = {'base_upc', 'source_base_upc'}
DATES = ('removed_at', 'created_at', 'paid_time', 'updated_at', 'import_date',
         'changed_at', 'scanned_at', 'time_removed', 'deleted_at', 'return_date', 'added_at', 'last_updated')


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def upc_key(value):
    value = str(value or '').strip().casefold()
    base, sep, suffix = value.partition('-')
    if re.fullmatch(r'\d+\.0', base):
        base = base[:-2]
    if base.isdigit():
        base = base.lstrip('0') or '0'
    return base + sep + suffix


def _plans(conn, upc, family):
    target = upc_key(upc)
    base = target.split('-', 1)[0]

    def matches(value):
        key = upc_key(value)
        return bool(key) and (key.split('-', 1)[0] == base if family else key == target)

    conn.create_function('finder_upc_matches', 1, matches, deterministic=True)

    def snapshot_matches(raw):
        try:
            pending = [json.loads(raw)]
            while pending:
                value = pending.pop()
                if isinstance(value, dict):
                    has_item_key = bool((UPC_COLUMNS - BASE_COLUMNS).intersection(key.lower() for key in value))
                    for key, child in value.items():
                        key = key.lower()
                        if key in UPC_COLUMNS and not (key in BASE_COLUMNS and not family and (has_item_key or '-' in target)) and matches(child):
                            return True
                        if isinstance(child, (dict, list)):
                            pending.append(child)
                elif isinstance(value, list):
                    pending.extend(value)
        except (ValueError, TypeError, RecursionError):
            pass
        return False

    conn.create_function('finder_snapshot_matches', 1, snapshot_matches, deterministic=True)
    schemas, plans = {}, {}
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        info = conn.execute(f'PRAGMA table_info({quote(table)})').fetchall()
        columns = {row[1].lower(): row[1] for row in info}
        schemas[table] = (columns, [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]])
        keys = UPC_COLUMNS.intersection(columns)
        # A base_upc is context, not proof that a particular suffixed unit matches.
        if not family and ('-' in target or keys - BASE_COLUMNS):
            keys -= BASE_COLUMNS
        if keys:
            plans[table] = (' OR '.join(f'finder_upc_matches({quote(columns[key])})' for key in sorted(keys)), [])
        elif {'data_json', 'before_json', 'source_row_json', 'payload_json'}.intersection(columns):
            snapshots = {'data_json', 'before_json', 'source_row_json', 'payload_json'}.intersection(columns)
            plans[table] = (' OR '.join(f'finder_snapshot_matches({quote(columns[key])})' for key in sorted(snapshots)), [])

    # Include child records that carry a parent ID instead of their own UPC.
    links = [('return_lifecycle_events', 'return_id', 'returns', 'id'),
             ('order_removal_allocations', 'order_row_id', 'orders', 'id'),
             ('ready_to_ship_notes', 'order_row_id', 'orders', 'id'),
             ('ready_to_ship_order_labels', 'order_row_id', 'orders', 'id')]
    for table in schemas:
        foreign_keys = conn.execute(f'PRAGMA foreign_key_list({quote(table)})').fetchall()
        for fk in foreign_keys:
            if sum(other[0] == fk[0] for other in foreign_keys) == 1 and fk[4]:
                links.append((table, fk[3], fk[2], fk[4]))
    direct = dict(plans)
    for child, child_key, parent, parent_key in dict.fromkeys(links):
        if child not in schemas or parent not in direct:
            continue
        if child_key.lower() not in schemas[child][0] or parent_key.lower() not in schemas[parent][0]:
            continue
        parent_where, params = direct[parent]
        clause = f'{quote(child_key)} IN (SELECT {quote(parent_key)} FROM {quote(parent)} WHERE {parent_where})'
        if child in plans:
            old, old_params = plans[child]
            plans[child] = (f'({old}) OR ({clause})', old_params + params)
        else:
            plans[child] = (clause, params)
    # FBA item records point to batches/sessions containing shipment context.
    for parent, parent_key, child, child_key in [
        ('fba_prep_batches', 'id', 'fba_prep_items', 'batch_id'),
        ('fba_prep_sessions', 'id', 'fba_prep_batches', 'session_id'),
        ('fba_prep_sessions', 'id', 'fba_pack_scans', 'session_id'),
        ('fba_prep_sessions', 'id', 'fba_count_scans', 'session_id'),
    ]:
        if parent not in schemas or child not in plans or child_key not in schemas[child][0]:
            continue
        child_where, params = plans[child]
        clause = f'{quote(parent_key)} IN (SELECT {quote(child_key)} FROM {quote(child)} WHERE {child_where})'
        if parent in plans:
            old, old_params = plans[parent]
            plans[parent] = (f'({old}) OR ({clause})', old_params + params)
        else:
            plans[parent] = (clause, params)
    return schemas, plans


def collect_records(base_dir, connect_db, upc, family=False, source=None, offset=0, limit=25):
    """Return all matching sources, or another page from one validated source.

    Schema discovery supports older Pi databases without migrations. Missing
    databases are reported and never created. No stock or history is changed.
    """
    if not upc_key(upc) or len(upc) > 160:
        raise ValueError('Enter a UPC (up to 160 characters).')
    if source and (':' not in source or source.split(':', 1)[0] not in DATABASES):
        raise ValueError('Unknown data source.')
    result = {'upc': upc, 'family': family, 'sources': [], 'unavailable': [], 'errors': []}
    # These exact marketplace keys link price history, which has no UPC column.
    listing_keys = {'amazon': set(), 'ebay': set()}
    requested_db = source.split(':', 1)[0] if source else None
    dependencies = {'amazonStore.db', 'ebayStore.db', 'listinglog.db', 'listagent.db'} if requested_db == 'pricemaster.db' else set()
    for database, label in DATABASES.items():
        if source and database != requested_db and database not in dependencies:
            continue
        if not (base_dir / database).is_file():
            result['unavailable'].append(label)
            continue
        try:
            with connect_db(database) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute('PRAGMA query_only = ON')
                schemas, plans = _plans(conn, upc, family)
                for platform, table, key in [('amazon', 'ITEMS', 'sku'), ('ebay', 'INVENTORY', 'itemid')]:
                    expected_db = 'amazonStore.db' if platform == 'amazon' else 'ebayStore.db'
                    if database != expected_db or table not in plans or key not in schemas[table][0]:
                        continue
                    where, params = plans[table]
                    listing_keys[platform].update(str(row[0]) for row in conn.execute(
                        f'SELECT DISTINCT {quote(schemas[table][0][key])} FROM {quote(table)} WHERE {where}', params
                    ) if row[0])
                # Keep historical price links even after a live listing disappears.
                for table, platform_col, amazon_col, ebay_col in [
                    ('listing_log', 'platform', 'sku', 'listing_id'),
                    ('listing_queue', 'listed_platform', 'listed_sku', 'listed_listing_id'),
                ]:
                    if table not in plans:
                        continue
                    columns = schemas[table][0]
                    if not {platform_col, amazon_col, ebay_col} <= columns.keys():
                        continue
                    where, params = plans[table]
                    selected = ', '.join(quote(columns[key]) for key in (platform_col, amazon_col, ebay_col))
                    for row in conn.execute(f'SELECT DISTINCT {selected} FROM {quote(table)} WHERE {where}', params):
                        platform = str(row[0] or '').lower()
                        key = row[1] if platform == 'amazon' else row[2]
                        if platform in listing_keys and key:
                            listing_keys[platform].add(str(key))
                if database == 'pricemaster.db':
                    for table in ('price_changes', 'listing_meta'):
                        if table not in schemas or not {'platform', 'listing_key'} <= schemas[table][0].keys():
                            continue
                        clauses, params = [], []
                        for platform, keys in listing_keys.items():
                            if keys:
                                clauses.append('(platform = ? AND listing_key IN (' + ','.join('?' for _ in keys) + '))')
                                params.extend([platform, *sorted(keys)])
                        if clauses:
                            plans[table] = (' OR '.join(clauses), params)
                for table, (where, params) in plans.items():
                    source_id = database + ':' + table
                    if source and source != source_id:
                        continue
                    try:
                        total = conn.execute(f'SELECT COUNT(*) FROM {quote(table)} WHERE {where}', params).fetchone()[0]
                        if not total and not source:
                            continue
                        columns, primary = schemas[table]
                        order = [columns[key] for key in DATES if key in columns][:1]
                        order += primary or ([columns['id']] if 'id' in columns else list(columns.values()))
                        ordering = ', '.join(quote(key) + ' DESC' for key in dict.fromkeys(order))
                        rows = conn.execute(f'SELECT * FROM {quote(table)} WHERE {where} ORDER BY {ordering} LIMIT ? OFFSET ?',
                                            [*params, limit, offset]).fetchall()
                        # SQLite BLOBs are represented losslessly rather than breaking JSON serialization.
                        records = [{key: ({'hex': value.hex()} if isinstance(value, bytes) else value)
                                    for key, value in dict(row).items()} for row in rows]
                        result['sources'].append({'id': source_id, 'label': label + ' · ' + table.replace('_', ' '),
                                                  'total': total, 'offset': offset, 'records': records,
                                                  'next_offset': offset + len(rows) if offset + len(rows) < total else None})
                    except sqlite3.Error:
                        result['errors'].append(label + ' · ' + table + ': could not read records.')
        except sqlite3.Error:
            result['errors'].append(label + ': database is unavailable or could not be read.')
    if source and not result['sources'] and not result['errors'] and not result['unavailable']:
        raise ValueError('Unknown data source.')
    return result
