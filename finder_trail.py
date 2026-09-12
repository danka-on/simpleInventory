"""Read-only item trail: one merged timeline, ledger totals, and clues for a UPC.

The Finder uses this when the warehouse flow has failed and someone must work
out where a unit went. Every source is optional and discovered by schema so
older Pi databases keep working; nothing is created or changed.
"""
import json
import re
import sqlite3

from finder_records import upc_key, quote

EVENT_LIMIT = 400

# removal_type -> event kind. Unknown types fall back to the sign of the delta.
LEDGER_KINDS = {
    'add_to_shelf': 'added', 'inventory_added': 'added',
    'locationmoved': 'moved', 'location_change': 'moved',
    'inventoryremoved': 'pulled', 'manual_handled': 'pulled', 'manual_sold_removal': 'pulled',
    'finder_removal': 'pulled', 'automatic': 'pulled', 'automatic_allocated': 'pulled',
    'repair_removal': 'pulled',
    'manual_edit': 'adjusted', 'quantity_adjustment': 'adjusted',
    'locationcleared': 'cleared',
    'legacy_zero_cleanup': 'deleted', 'manual_multidb_delete': 'deleted', 'prep_history_delete': 'deleted',
}
CATEGORY = {
    'added': 'inventory', 'moved': 'inventory', 'pulled': 'inventory', 'adjusted': 'inventory',
    'cleared': 'inventory', 'deleted': 'inventory', 'undo': 'inventory', 'archived': 'inventory',
    'sold': 'sales', 'returned': 'sales', 'matched': 'sales',
    'received': 'bol', 'bol': 'bol',
    'prep': 'prep', 'prep_status': 'prep', 'prep_note': 'prep',
    'fba': 'fba', 'listed': 'listings',
}
DATABASES = {
    'searchRack.db': 'Warehouse and FBA prep', 'rackhistory.db': 'Rack history',
    'sold.db': 'Sold history and returns', 'preplog.db': 'Prep history', 'bol.db': 'Prep details and BOL',
    'rawbol.db': 'Imported BOL', 'deleted.db': 'Deleted inventory', 'listinglog.db': 'Listing history',
}
SNAPSHOT_KEYS = {'barcode', 'upc', 'source_upc', 'item_barcode', 'product_upc'}


def display_upc(value):
    text = str(value or '').strip()
    base, sep, suffix = text.partition('-')
    if re.fullmatch(r'\d+\.0', base):
        base = base[:-2]
    if base.isdigit():
        base = base.lstrip('0') or '0'
        if len(base) <= 12:
            base = base.zfill(12)
    return base + sep + suffix


def _matcher(upc, family):
    target = upc_key(upc)
    base = target.split('-', 1)[0]

    def matches(value):
        key = upc_key(value)
        if not key:
            return False
        return key.split('-', 1)[0] == base if family else key == target

    return matches


def _snapshot_matcher(matches):
    def snapshot_matches(raw):
        try:
            pending = [json.loads(raw)]
        except (ValueError, TypeError):
            return False
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                for key, child in value.items():
                    if str(key).lower() in SNAPSHOT_KEYS and matches(child):
                        return True
                    if isinstance(child, (dict, list)):
                        pending.append(child)
            elif isinstance(value, list):
                pending.extend(value)
        return False

    return snapshot_matches


def _columns(conn, table):
    info = conn.execute(f'PRAGMA table_info({quote(table)})').fetchall()
    return {row[1].lower(): row[1] for row in info}


def _clean(value):
    if isinstance(value, bytes):
        return {'hex': value.hex()}
    return value


def _rows(conn, table, where, params=(), order='', limit=EVENT_LIMIT):
    sql = f'SELECT * FROM {quote(table)} WHERE {where}'
    if order:
        sql += f' ORDER BY {order}'
    sql += ' LIMIT ?'
    cursor = conn.execute(sql, [*params, limit])
    names = [column[0] for column in cursor.description]
    return [{name: _clean(value) for name, value in zip(names, row)} for row in cursor.fetchall()]


def _text(value):
    text = str(value if value is not None else '').strip()
    return '' if text.lower() in ('nan', 'none', 'null') else text


def _int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _money(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def sort_key(timestamp):
    """Order mixed ISO / SQL / date-only timestamps; offsets are ignored."""
    text = _text(timestamp).replace(' ', 'T')
    match = re.match(r'(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?))?', text)
    if not match:
        return ''
    time = (match.group(2) or '00:00:00')
    if len(time) == 5:
        time += ':00'
    return match.group(1) + 'T' + time


def _event(kind, source_id, record, timestamp, title, **fields):
    row_id = record.get('id', record.get('ID'))
    event = {
        'id': f"{source_id}:{row_id}", 'kind': kind, 'category': CATEGORY.get(kind, 'other'),
        'ts': _text(timestamp), 'sort': sort_key(timestamp), 'title': title, 'detail': '',
        'position': '', 'from_position': '', 'to_position': '', 'qty': None, 'delta': None,
        'order_id': '', 'store': '', 'lot': '', 'status': '', 'note': '', 'undone': False, 'pending': False,
        'upc': '', 'source_id': source_id, 'record': record,
    }
    event.update(fields)
    return event


def _unit_word(count):
    return f'{count} unit' if count == 1 else f'{count} units'


def _ledger_event(row):
    removal_type = _text(row.get('removal_type')).lower()
    old_qty = row.get('old_quantity')
    new_qty = row.get('new_quantity')
    delta = (_int(new_qty) - _int(old_qty)) if old_qty is not None and new_qty is not None else None
    kind = LEDGER_KINDS.get(removal_type)
    if removal_type.endswith('_undo'):
        kind = 'undo'
    if not kind:
        kind = 'added' if (delta or 0) > 0 else 'pulled' if (delta or 0) < 0 else 'adjusted'
    moved = _int(row.get('quantity_removed'), abs(delta or 0))
    position = _text(row.get('item_position'))
    source = _text(row.get('from_position'))
    target = _text(row.get('to_position'))
    order_id = _text(row.get('order_id'))
    undone = bool(_text(row.get('undone_at')))
    status = _text(row.get('event_status')).lower() or 'applied'
    pending = status != 'applied'
    where = position or source
    if kind == 'added':
        title = f'Added {_unit_word(moved or abs(delta or 0))}' + (f' to {target or position}' if (target or position) else '')
    elif kind == 'moved':
        if source and target and source.lower() != target.lower():
            title = f'Moved {_unit_word(moved)} {source} → {target}'
        elif target:
            title = f'Moved {_unit_word(moved)} to {target}'
        else:
            title = f'Moved {_unit_word(moved)}' + (f' out of {where}' if where else '')
    elif kind == 'pulled':
        title = f'Pulled {_unit_word(moved)}' + (f' from {where}' if where else '')
        if order_id:
            title += f' for order {order_id}'
    elif kind == 'adjusted':
        if delta is not None:
            title = f'Quantity {_int(old_qty)} → {_int(new_qty)}' + (f' at {where}' if where else '')
        else:
            title = 'Quantity adjusted' + (f' at {where}' if where else '')
    elif kind == 'cleared':
        title = f'Location cleared' + (f' at {where}' if where else '') + (f' ({_unit_word(moved)})' if moved else '')
    elif kind == 'deleted':
        title = 'Shelf record deleted' + (f' at {where}' if where else '')
        if moved:
            title += f' with {_unit_word(moved)}'
    else:
        title = f'Undo: restored {_unit_word(moved or abs(delta or 0))}' + (f' to {target or position}' if (target or position) else '')
    detail = removal_type.replace('_', ' ')
    if delta is not None and kind not in ('adjusted',):
        detail += f' · {_int(old_qty)} → {_int(new_qty)}'
    if row.get('inventory_row_deleted') in (1, '1', True):
        detail += ' · row removed from shelf'
    return _event(kind, 'rackhistory.db:removed_items', row, row.get('removed_at'), title, detail=detail,
                  position=position, from_position=source, to_position=target, qty=moved, delta=delta,
                  order_id=order_id, status=status, undone=undone, pending=pending, upc=_text(row.get('barcode')))


def collect_trail(base_dir, connect_db, upc, family=False):
    """Return identity, current stock, ledger totals, clues, and a merged timeline."""
    if not upc_key(upc) or len(upc) > 160:
        raise ValueError('Enter a UPC (up to 160 characters).')
    matches = _matcher(upc, family)
    snapshot_matches = _snapshot_matcher(matches)
    result = {
        'upc': upc, 'upc_display': display_upc(upc), 'family': family,
        'identity': {'title': '', 'image': '', 'aliases': [], 'lots': [], 'custom': False},
        'stock': [], 'events': [], 'flags': [], 'unavailable': [], 'errors': [],
        'ledger': {
            'received': 0, 'shelf_adds': 0, 'pulled': 0, 'moves': 0, 'adjustments': 0, 'sold_units': 0,
            'sold_orders': 0, 'sold_unpulled_units': 0, 'sold_unpulled_orders': [], 'returned': 0, 'restocked': 0,
            'fba_units': 0, 'fba_sessions': [], 'on_shelf': 0, 'deleted_rows': 0, 'pending_events': 0,
            'undone_events': 0, 'no_location_units': 0, 'pending_deletion_rows': 0,
        },
        'last_seen': None, 'last_exit': None,
    }
    identity = result['identity']
    ledger = result['ledger']
    events = result['events']
    fallback_title, fallback_image = [], []

    def prepared(database):
        if not (base_dir / database).is_file():
            result['unavailable'].append(DATABASES[database])
            return False
        return True

    def bind(conn):
        conn.row_factory = None
        conn.execute('PRAGMA query_only = ON')
        conn.create_function('finder_upc_matches', 1, matches, deterministic=True)
        conn.create_function('finder_snapshot_matches', 1, snapshot_matches, deterministic=True)

    def read(database, worker):
        if not prepared(database):
            return
        try:
            with connect_db(database) as conn:
                bind(conn)
                worker(conn)
        except sqlite3.Error:
            result['errors'].append(DATABASES[database] + ': could not be read.')

    def where_for(columns, *names):
        keys = [columns[name] for name in names if name in columns]
        if not keys:
            return None
        return ' OR '.join(f'finder_upc_matches({quote(key)})' for key in keys)

    # --- Warehouse: current stock, archived rows, FBA -----------------------------------------
    def warehouse(conn):
        columns = _columns(conn, 'SEARCHRACK')
        if 'barcode' in columns:
            pending_deletion = {}
            if 'zero_qty_pending_deletion' in _tables(conn):
                for searchrack_id, delete_at, cancelled in conn.execute(
                        'SELECT searchrack_id, delete_at, deletion_cancelled FROM zero_qty_pending_deletion'):
                    if not cancelled:
                        pending_deletion[searchrack_id] = _text(delete_at)
            rows = _rows(conn, 'SEARCHRACK', 'finder_upc_matches(BARCODE)', order='QUANTITY DESC, ID', limit=200)
            for row in rows:
                quantity = _int(row.get('QUANTITY'))
                position = _text(row.get('ITEM_POSITION'))
                picture = _text(row.get('PICTUREPOSITION'))
                if picture and (not position or position.lower() == 'picture'):
                    position = picture
                entry = {
                    'searchrack_id': row.get('ID'), 'quantity': quantity, 'position': position,
                    'pictureposition': picture, 'title': _text(row.get('TITLE')), 'image': _text(row.get('IMAGE')),
                    'warehouse_note': _text(row.get('WAREHOUSE_NOTE')), 'upc': _text(row.get('BARCODE')),
                    'upc_display': display_upc(row.get('BARCODE')), 'custom': row.get('CUSTOM_TITLE') in (1, '1', True),
                    'pending_deletion': pending_deletion.get(row.get('ID'), ''), 'created_at': _text(row.get('CREATED_AT')),
                }
                result['stock'].append(entry)
                if quantity > 0:
                    ledger['on_shelf'] += quantity
                    if not position:
                        ledger['no_location_units'] += quantity
                if entry['pending_deletion']:
                    ledger['pending_deletion_rows'] += 1
                if entry['custom']:
                    identity['custom'] = True
                if entry['title'] and not identity['title'] and quantity > 0:
                    identity['title'] = entry['title']
                if entry['image'] and not identity['image'] and quantity > 0:
                    identity['image'] = entry['image']
                if entry['title']:
                    fallback_title.append(entry['title'])
                if entry['image']:
                    fallback_image.append(entry['image'])
        tables = _tables(conn)
        if 'archived_searchrack' in tables:
            columns = _columns(conn, 'archived_searchrack')
            where = where_for(columns, 'barcode')
            if where:
                for row in _rows(conn, 'archived_searchrack', where, order='id DESC'):
                    position = _text(row.get('item_position'))
                    events.append(_event(
                        'archived', 'searchRack.db:archived_searchrack', row, row.get('archived_at'),
                        'Shelf record archived' + (f' from {position}' if position else ''),
                        detail=_text(row.get('reason')), position=position, qty=_int(row.get('quantity')),
                        upc=_text(row.get('barcode'))))
                    if _text(row.get('title')):
                        fallback_title.append(_text(row.get('title')))
        if 'fba_prep_items' in tables:
            columns = _columns(conn, 'fba_prep_items')
            where = where_for(columns, 'barcode')
            if where:
                batches = {}
                if 'fba_prep_batches' in tables:
                    session_names = {}
                    if 'fba_prep_sessions' in tables:
                        session_names = {row[0]: (_text(row[1]), _text(row[2])) for row in conn.execute(
                            'SELECT id, session_name, shipment_id FROM fba_prep_sessions')}
                    for row in conn.execute('SELECT id, batch_name, shipment_id, session_id, status FROM fba_prep_batches'):
                        session = session_names.get(row[3], ('', ''))
                        batches[row[0]] = {'batch': _text(row[1]), 'shipment': _text(row[2]) or session[1],
                                           'session': session[0], 'status': _text(row[4])}
                for row in _rows(conn, 'fba_prep_items', where, order='id DESC'):
                    batch = batches.get(row.get('batch_id'), {})
                    units = _int(row.get('quantity_removed')) or _int(row.get('requested_quantity'))
                    label = batch.get('session') or batch.get('batch') or f"batch {row.get('batch_id')}"
                    positions = _fba_positions(row.get('source_locations_json'))
                    ledger['fba_units'] += _int(row.get('quantity_removed'))
                    if label and label not in ledger['fba_sessions']:
                        ledger['fba_sessions'].append(label)
                    detail = ' · '.join(part for part in [
                        f"shipment {batch['shipment']}" if batch.get('shipment') else '',
                        f"box {row.get('box_number')}" if _text(row.get('box_number')) else '',
                        f"status {batch['status']}" if batch.get('status') else '',
                        f"from {', '.join(positions)}" if positions else '',
                    ] if part)
                    events.append(_event(
                        'fba', 'searchRack.db:fba_prep_items', row, row.get('created_at'),
                        f'Sent {_unit_word(units)} to FBA · {label}', detail=detail,
                        position=positions[0] if positions else '', qty=units, upc=_text(row.get('barcode'))))
    read('searchRack.db', warehouse)

    # --- Ledger --------------------------------------------------------------------------------
    def rack_history(conn):
        if 'removed_items' not in _tables(conn):
            return
        columns = _columns(conn, 'removed_items')
        where = where_for(columns, 'barcode')
        if not where:
            return
        for row in _rows(conn, 'removed_items', where, order='id DESC'):
            event = _ledger_event(row)
            events.append(event)
            if event['undone']:
                ledger['undone_events'] += 1
                continue
            if event['pending']:
                ledger['pending_events'] += 1
                continue
            delta = event['delta'] or 0
            if event['kind'] == 'added':
                ledger['shelf_adds'] += delta if delta > 0 else (event['qty'] or 0)
            elif event['kind'] == 'pulled':
                ledger['pulled'] += abs(delta) if delta < 0 else (event['qty'] or 0)
            elif event['kind'] == 'moved':
                ledger['moves'] += 1
            elif event['kind'] in ('adjusted', 'cleared', 'deleted', 'undo'):
                ledger['adjustments'] += delta
            if _text(row.get('title')):
                fallback_title.append(_text(row.get('title')))
    read('rackhistory.db', rack_history)

    # --- Sales ---------------------------------------------------------------------------------
    def sales(conn):
        tables = _tables(conn)
        if 'orders' in tables:
            columns = _columns(conn, 'orders')
            where = where_for(columns, 'barcode', 'source_upc')
            if where:
                for row in _rows(conn, 'orders', where, order='id DESC'):
                    quantity = _int(row.get('quantity'), 1)
                    store = _text(row.get('store'))
                    order_id = _text(row.get('order_id')) or str(row.get('id'))
                    pulled = row.get('rackupdated') in (1, '1', True)
                    cancelled = row.get('removal_cancelled') in (1, '1', True)
                    location = _text(row.get('location'))
                    status = _text(row.get('checkout_status'))
                    ledger['sold_units'] += quantity
                    ledger['sold_orders'] += 1
                    if not pulled and not cancelled:
                        ledger['sold_unpulled_units'] += quantity
                        ledger['sold_unpulled_orders'].append(order_id)
                    detail = ' · '.join(part for part in [
                        'pulled from shelf' if pulled else ('removal cancelled' if cancelled else 'not pulled from shelf yet'),
                        status, f'condition {_text(row.get("item_condition"))}' if _text(row.get('item_condition')) else '',
                        f'LOT {_text(row.get("lot_number"))}' if _text(row.get('lot_number')) else '',
                        f'shipped {_text(row.get("shipped_time"))[:10]}' if _text(row.get('shipped_time')) else '',
                    ] if part)
                    events.append(_event(
                        'sold', 'sold.db:orders', row, row.get('paid_time') or row.get('shipped_time'),
                        f'Sold {_unit_word(quantity)}' + (f' on {store}' if store else '') + f' · order {order_id}',
                        detail=detail, position=location, qty=quantity, order_id=order_id, store=store,
                        status='pulled' if pulled else ('cancelled' if cancelled else 'unpulled'),
                        lot=_text(row.get('lot_number')), upc=_text(row.get('barcode')), price=_money(row.get('price'))))
                    if _text(row.get('title')):
                        fallback_title.append(_text(row.get('title')))
                    if _text(row.get('image')):
                        fallback_image.append(_text(row.get('image')))
        if 'returns' in tables:
            columns = _columns(conn, 'returns')
            where = where_for(columns, 'barcode')
            if where:
                for row in _rows(conn, 'returns', where, order='id DESC'):
                    quantity = _int(row.get('quantity'), 1)
                    restocked = row.get('restocked') in (1, '1', True)
                    ledger['returned'] += quantity
                    if restocked:
                        ledger['restocked'] += quantity
                    detail = ' · '.join(part for part in [
                        'restocked' if restocked else 'not restocked',
                        _text(row.get('condition_received')), _text(row.get('return_reason')),
                        f'refund ${_money(row.get("refund_amount")):.2f}' if _money(row.get('refund_amount')) is not None else '',
                        'relisted' if row.get('relisted') in (1, '1', True) else '',
                        'resold' if row.get('resold') in (1, '1', True) else '',
                    ] if part)
                    events.append(_event(
                        'returned', 'sold.db:returns', row, row.get('return_date') or row.get('created_at'),
                        f'Returned {_unit_word(quantity)}' + (f' on {_text(row.get("store"))}' if _text(row.get('store')) else '')
                        + (f' · order {_text(row.get("order_id"))}' if _text(row.get('order_id')) else ''),
                        detail=detail, position=_text(row.get('location')), qty=quantity,
                        order_id=_text(row.get('order_id')), store=_text(row.get('store')),
                        status='restocked' if restocked else 'not restocked', note=_text(row.get('notes')),
                        upc=_text(row.get('barcode'))))
        if 'order_finder_matches' in tables:
            columns = _columns(conn, 'order_finder_matches')
            where = where_for(columns, 'barcode')
            if where:
                order_ids = {}
                if 'orders' in tables:
                    order_ids = dict(conn.execute('SELECT id, order_id FROM orders'))
                for row in _rows(conn, 'order_finder_matches', where, order='id DESC'):
                    order_id = _text(order_ids.get(row.get('order_row_id'))) or str(row.get('order_row_id'))
                    location = _text(row.get('location'))
                    events.append(_event(
                        'matched', 'sold.db:order_finder_matches', row, row.get('updated_at') or row.get('created_at'),
                        f'Matched to shelf' + (f' {location}' if location else '') + f' for order {order_id}',
                        detail='Finder match saved for Ready to Ship', position=location, order_id=order_id,
                        upc=_text(row.get('barcode'))))
    read('sold.db', sales)

    # --- Prep ----------------------------------------------------------------------------------
    def prep_history(conn):
        if 'prep_log' not in _tables(conn):
            return
        columns = _columns(conn, 'prep_log')
        where = where_for(columns, 'upc', 'base_upc') if family else where_for(columns, 'upc')
        if not where:
            return
        for row in _rows(conn, 'prep_log', where, order='id DESC'):
            status = _text(row.get('status')).lower()
            undone = row.get('undone') in (1, '1', True) or bool(_text(row.get('undone_at')))
            quantity = _int(row.get('quantity'), 0)
            detail = ' · '.join(part for part in [
                _text(row.get('reason')), _text(row.get('source')), f'quantity {quantity}' if quantity else '',
            ] if part)
            events.append(_event(
                'prep', 'preplog.db:prep_log', row, row.get('created_at'),
                f'Prep check: {status or "logged"}', detail=detail, status=status, qty=quantity or None,
                note=_text(row.get('note')), undone=undone, upc=_text(row.get('upc'))))
            if undone:
                ledger['undone_events'] += 1
    read('preplog.db', prep_history)

    def prep_details(conn):
        tables = _tables(conn)
        if 'items_prep_status' in tables:
            columns = _columns(conn, 'items_prep_status')
            where = where_for(columns, 'upc')
            if where:
                for row in _rows(conn, 'items_prep_status', where, order='id DESC'):
                    status = _text(row.get('status')).lower()
                    lot = _text(row.get('lot_number'))
                    position = _text(row.get('location')) or _text(row.get('pictureposition'))
                    events.append(_event(
                        'prep_status', 'bol.db:items_prep_status', row, row.get('updated_at'),
                        f'Prep status: {status or "unknown"}' + (f' · LOT {lot}' if lot else ''),
                        detail=_text(row.get('reason')), status=status, lot=lot, position=position,
                        qty=_int(row.get('quantity')) or None, note=_text(row.get('note')), upc=_text(row.get('upc'))))
        if 'items_prep_notes' in tables:
            columns = _columns(conn, 'items_prep_notes')
            where = where_for(columns, 'upc')
            if where:
                for row in _rows(conn, 'items_prep_notes', where, order='id DESC'):
                    if _text(row.get('row_status')).lower() in ('deleted', 'removed'):
                        continue
                    events.append(_event(
                        'prep_note', 'bol.db:items_prep_notes', row, row.get('created_at'), 'Prep note',
                        note=_text(row.get('note')), upc=_text(row.get('upc'))))
        if 'bol_items' in tables:
            columns = _columns(conn, 'bol_items')
            where = where_for(columns, 'upc')
            if where:
                for row in _rows(conn, 'bol_items', where, order='id DESC'):
                    lot = _text(row.get('lot_number'))
                    quantity = _int(row.get('quantity'))
                    listed = [name for name, flag in (('Amazon', row.get('listed_amazon')), ('eBay', row.get('listed_ebay')),
                                                      ('Facebook', row.get('listed_facebook'))) if flag in (1, '1', True)]
                    detail = ' · '.join(part for part in [
                        f'good {_int(row.get("good_qty"))} / bad {_int(row.get("bad_qty"))} / unchecked {_int(row.get("unchecked_qty"))}'
                        if any(name in columns for name in ('good_qty', 'bad_qty', 'unchecked_qty')) else '',
                        'found on BOL scan' if row.get('isFound') in (1, '1', True) else '',
                        'prepped' if row.get('itemprepped') in (1, '1', True) else '',
                        f'listed on {", ".join(listed)}' if listed else '',
                        _text(row.get('list_status')),
                    ] if part)
                    events.append(_event(
                        'bol', 'bol.db:bol_items', row, row.get('import_date'),
                        f'On BOL worklist · {_unit_word(quantity)}' + (f' · LOT {lot}' if lot else ''),
                        detail=detail, lot=lot, qty=quantity, upc=_text(row.get('upc'))))
                    if lot and lot not in identity['lots']:
                        identity['lots'].append(lot)
                    if _text(row.get('item_description')):
                        fallback_title.append(_text(row.get('item_description')))
                    if _text(row.get('image_url')):
                        fallback_image.append(_text(row.get('image_url')))
    read('bol.db', prep_details)

    # --- Receiving -----------------------------------------------------------------------------
    def receiving(conn):
        if 'raw_bol_items' not in _tables(conn):
            return
        columns = _columns(conn, 'raw_bol_items')
        where = where_for(columns, 'upc')
        if not where:
            return
        for row in _rows(conn, 'raw_bol_items', where, order='id DESC'):
            quantity = max(_int(row.get('quantity'), 1), 0)
            lot = _text(row.get('lot_number'))
            ledger['received'] += quantity
            if lot and lot not in identity['lots']:
                identity['lots'].append(lot)
            detail = ' · '.join(part for part in [
                f'BOL location {_text(row.get("bol_location"))}' if _text(row.get('bol_location')) else '',
                f'cost ${_money(row.get("avg_cost")):.2f}' if _money(row.get('avg_cost')) is not None else '',
                f'retail ${_money(row.get("original_retail")):.2f}' if _money(row.get('original_retail')) is not None else '',
            ] if part)
            events.append(_event(
                'received', 'rawbol.db:raw_bol_items', row, row.get('import_date') or row.get('created_at'),
                f'Received {_unit_word(quantity)}' + (f' · LOT {lot}' if lot else ''), detail=detail, lot=lot,
                qty=quantity, upc=_text(row.get('upc'))))
            if _text(row.get('item_description')):
                fallback_title.append(_text(row.get('item_description')))
            if _text(row.get('image_url')):
                fallback_image.append(_text(row.get('image_url')))
    read('rawbol.db', receiving)

    # --- Deleted snapshots ---------------------------------------------------------------------
    def deleted(conn):
        if 'deleted_items' not in _tables(conn):
            return
        columns = _columns(conn, 'deleted_items')
        if 'data_json' not in columns:
            return
        for row in _rows(conn, 'deleted_items', 'finder_snapshot_matches(data_json)', order='id DESC'):
            try:
                snapshot = json.loads(row.get('data_json') or '{}')
            except (ValueError, TypeError):
                snapshot = {}
            if not isinstance(snapshot, dict):
                snapshot = {}
            lowered = {str(key).lower(): value for key, value in snapshot.items()}
            position = _text(lowered.get('item_position') or lowered.get('location'))
            quantity = _int(lowered.get('quantity'))
            table = _text(row.get('source_table'))
            source_db = _text(row.get('source_db'))
            if table.lower() == 'searchrack':
                title = 'Shelf record deleted' + (f' from {position}' if position else '') + (f' with {_unit_word(quantity)}' if quantity else '')
            else:
                title = f'Deleted from {source_db} {table}'.strip()
            events.append(_event(
                'deleted', 'deleted.db:deleted_items', row, row.get('deleted_at'), title,
                detail=f'{source_db}.{table} row {row.get("source_pk") or row.get("source_id") or ""}'.strip(),
                position=position, qty=quantity or None,
                upc=_text(lowered.get('barcode') or lowered.get('upc'))))
            if _text(lowered.get('title')):
                fallback_title.append(_text(lowered.get('title')))
            if _text(lowered.get('image')):
                fallback_image.append(_text(lowered.get('image')))
    read('deleted.db', deleted)

    # --- Listings ------------------------------------------------------------------------------
    def listings(conn):
        if 'listing_log' not in _tables(conn):
            return
        columns = _columns(conn, 'listing_log')
        where = where_for(columns, 'upc')
        if not where:
            return
        for row in _rows(conn, 'listing_log', where, order='id DESC', limit=100):
            platform = _text(row.get('platform'))
            action = _text(row.get('action')).replace('_', ' ')
            success = row.get('success') in (1, '1', True)
            detail = ' · '.join(part for part in [
                _text(row.get('sku')), f'price ${_money(row.get("price")):.2f}' if _money(row.get('price')) is not None else '',
                f'quantity {_int(row.get("quantity"))}' if _text(row.get('quantity')) else '', _text(row.get('error')),
            ] if part)
            events.append(_event(
                'listed', 'listinglog.db:listing_log', row, row.get('created_at'),
                f'{platform or "Listing"} {action or "activity"}' + ('' if success else ' failed'),
                detail=detail, status='ok' if success else 'failed', upc=_text(row.get('upc')), url=_text(row.get('url'))))
    read('listinglog.db', listings)

    # --- Aliases (confirmed alternate names) ---------------------------------------------------
    try:
        from finder_aliases import DATABASES as ALIAS_DATABASES, barcode_key
        key = barcode_key(upc)
        titles = []
        # Only open databases that already exist; a missing file must never be created here.
        for database in ALIAS_DATABASES.values():
            if not (base_dir / database).is_file():
                continue
            with connect_db(database) as conn:
                conn.execute('PRAGMA query_only = ON')
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='finder_aliases'").fetchone():
                    continue
                titles.extend(row[0] for row in conn.execute(
                    'SELECT title FROM finder_aliases WHERE barcode_key = ? ORDER BY rowid', (key,)))
        identity['aliases'] = list(dict.fromkeys(titles))
    except Exception:
        identity['aliases'] = []

    if not identity['title']:
        identity['title'] = next(iter(fallback_title), '')
    if not identity['image']:
        identity['image'] = next(iter(fallback_image), '')

    events.sort(key=lambda event: (event['sort'], _int(event['record'].get('id', event['record'].get('ID', 0)))), reverse=True)
    # The ledger row and the deleted.db snapshot describe one deletion; count moments, not records.
    ledger['deleted_rows'] = len({event['sort'][:16] for event in events
                                  if event['kind'] in ('deleted', 'archived') and not event['undone']})
    for event in events:
        if event['undone'] or event['pending']:
            continue
        place = event['position'] or event['to_position'] or event['from_position']
        if place and result['last_seen'] is None:
            result['last_seen'] = {'position': place, 'ts': event['ts'], 'kind': event['kind'], 'id': event['id']}
        if event['kind'] in ('pulled', 'deleted', 'archived', 'cleared', 'fba') and result['last_exit'] is None:
            result['last_exit'] = {'position': place, 'ts': event['ts'], 'kind': event['kind'], 'id': event['id'],
                                   'title': event['title'], 'order_id': event['order_id']}
        if result['last_seen'] and result['last_exit']:
            break
    result['flags'] = _flags(result)
    return result


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _fba_positions(raw):
    try:
        value = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        return []
    positions = []
    items = value if isinstance(value, list) else list(value.values()) if isinstance(value, dict) else []
    for item in items:
        if isinstance(item, dict):
            code = _text(item.get('location') or item.get('position') or item.get('item_position') or item.get('code'))
        else:
            code = _text(item)
        if code and code not in positions:
            positions.append(code)
    if not positions and isinstance(value, dict):
        positions = [key for key in value if _text(key)]
    return positions


def _flags(result):
    ledger = result['ledger']
    events = result['events']
    flags = []

    def ids(predicate):
        return [event['id'] for event in events if predicate(event)][:20]

    def add(level, text, event_ids=None):
        flags.append({'level': level, 'text': text, 'event_ids': event_ids or []})

    if ledger['sold_unpulled_units']:
        orders = ledger['sold_unpulled_orders']
        add('alert', f"{_unit_word(ledger['sold_unpulled_units'])} sold but never pulled from the shelf"
            + (f" (order {orders[0]})" if len(orders) == 1 else f" across {len(orders)} orders"),
            ids(lambda event: event['kind'] == 'sold' and event['status'] == 'unpulled'))
    if ledger['pending_events']:
        add('alert', f"{ledger['pending_events']} ledger event(s) are pending or failed and were never applied",
            ids(lambda event: event['pending']))
    if ledger['deleted_rows']:
        deletions = [event for event in events if event['kind'] in ('deleted', 'archived') and not event['undone']]
        last = deletions[0] if deletions else None
        add('warn', f"Shelf record deleted or archived {ledger['deleted_rows']} time(s)"
            + (f"; last on {last['ts'][:10]}" + (f" from {last['position']}" if last['position'] else '') if last else ''),
            [event['id'] for event in deletions[:20]])
    if ledger['no_location_units']:
        add('warn', f"{_unit_word(ledger['no_location_units'])} on the shelf with no location recorded")
    if ledger['pending_deletion_rows']:
        add('warn', f"{ledger['pending_deletion_rows']} zero-quantity row(s) scheduled for automatic deletion")
    if ledger['fba_units']:
        sessions = ', '.join(ledger['fba_sessions'][:3])
        add('info', f"{_unit_word(ledger['fba_units'])} sent to Amazon FBA" + (f" ({sessions})" if sessions else ''),
            ids(lambda event: event['kind'] == 'fba'))
    if ledger['received'] and ledger['received'] > ledger['shelf_adds']:
        gap = ledger['received'] - ledger['shelf_adds']
        add('info', f"Received {ledger['received']} on BOL but only {ledger['shelf_adds']} shelf add(s) recorded"
            + (f" — {gap} never scanned to a shelf" if ledger['shelf_adds'] else " — never scanned to a shelf"),
            ids(lambda event: event['kind'] == 'received'))
    if ledger['returned'] > ledger['restocked']:
        add('info', f"{_unit_word(ledger['returned'] - ledger['restocked'])} returned and not restocked",
            ids(lambda event: event['kind'] == 'returned' and event['status'] != 'restocked'))
    if ledger['undone_events']:
        add('info', f"{ledger['undone_events']} event(s) were undone", ids(lambda event: event['undone']))
    if not ledger['on_shelf'] and not events:
        add('info', 'No records anywhere for this UPC')
    return flags
