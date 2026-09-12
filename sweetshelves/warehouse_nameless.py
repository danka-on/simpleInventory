"""Warehouse items that carry nothing but a barcode."""

import datetime
import sqlite3
from flask import jsonify, render_template, request
from . import (
    caching as ss_caching, database as ss_database, errors as ss_errors, normalization as
    ss_normalization, warehouse_receiving as ss_warehouse_receiving,
)

# A screen that walks every distinct barcode through the naming gate stays cheap
# only while the backlog is small; a runaway warehouse reports the truncation
# instead of holding the request open.
NAMELESS_SCAN_LIMIT = 500

# Words a warehouse row can hold that still tell nobody what the item is. The
# first line mirrors the placeholders save_custom_item_identity refuses to
# store; the rest are older labels that reached rows before that gate existed.
EMPTY_TITLE_WORDS = (
    'unknown', 'warehouse item', 'item', 'n/a',
    'no barcode item', 'no barcode', 'custom item',
    'no title', 'no name', 'none', 'null', 'untitled', 'unnamed', '-', '--',
)


def _title_is_meaningless(title):
    """True when a stored title identifies nothing beyond the barcode itself."""
    text = ' '.join(str(title or '').split())
    if not text:
        return True
    if text.lower() in EMPTY_TITLE_WORDS:
        return True
    # A bare number is the barcode wearing the title column, not a name.
    return text.replace('-', '').replace(' ', '').isdigit()


def _manual_title_rejection(title):
    """Why a typed product name is unusable, or '' when it is a real name."""
    if len(title) > 200:
        return 'Use a short descriptive name of 200 characters or fewer.'
    if title and _title_is_meaningless(title):
        return 'Use a short descriptive name, not a barcode or placeholder.'
    return ''


def _age_days(created_at):
    parsed = ss_normalization._parse_iso_utc_naive(created_at)
    if not parsed:
        return None
    delta = datetime.datetime.utcnow() - parsed
    return max(0, delta.days)


def _base_barcode(barcode):
    base = str(barcode or '').split('-')[0].strip()
    return ss_normalization._strip_leading_zeros_numeric(base) or base


def _searchrack_columns(cur):
    return {row[1].lower(): row[1] for row in cur.execute('PRAGMA table_info(SEARCHRACK)')}


def _load_untitled_rows(cur):
    """Every in-stock warehouse row whose stored title says nothing."""
    cols = _searchrack_columns(cur)
    quantity_col = cols.get('quantity') or cols.get('qty')
    select = ['ID', 'TITLE', 'BARCODE', 'ITEM_POSITION']
    for optional in ('created_at', 'image', 'images', 'itemid', 'warehouse_note', 'custom_title'):
        if optional in cols:
            select.append(cols[optional])
    if quantity_col and quantity_col not in select:
        select.append(quantity_col)
    where = ''
    if quantity_col:
        where = f' WHERE COALESCE(CAST({ss_database._sqlite_ident(quantity_col)} AS INTEGER), 0) > 0'
    sql = f"SELECT {', '.join(ss_database._sqlite_ident(c) for c in select)} FROM SEARCHRACK{where}"

    rows = []
    for record in cur.execute(sql):
        item = dict(record)
        lowered = {key.lower(): value for key, value in item.items()}
        if not _title_is_meaningless(lowered.get('title')):
            continue
        barcode = str(lowered.get('barcode') or '').strip()
        if not barcode:
            # No barcode either: the add-item gate blocks these, and a naming
            # screen has nothing to key a name on.
            continue
        rows.append({
            'id': ss_normalization._coerce_int(lowered.get('id'), 0),
            'barcode': barcode,
            'stored_title': ' '.join(str(lowered.get('title') or '').split()),
            'location': str(lowered.get('item_position') or '').strip(),
            'quantity': ss_normalization._coerce_int(lowered.get('quantity') or lowered.get('qty'), 1),
            'created_at': str(lowered.get('created_at') or '').strip(),
            'age_days': _age_days(lowered.get('created_at')),
            'note': str(lowered.get('warehouse_note') or '').strip(),
            'has_image': bool(str(lowered.get('image') or lowered.get('images') or '').strip()),
            'has_item_id': bool(str(lowered.get('itemid') or '').strip()),
        })
    return rows


def _group_by_barcode(rows):
    groups = {}
    for row in rows:
        key = ss_normalization._normalize_scanned_upc(row['barcode']) or row['barcode']
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                'barcode': key,
                'base_barcode': _base_barcode(key),
                'display_barcode': ss_normalization._format_upc_display(key),
                'rows': [],
                'units': 0,
                'locations': [],
                'suggested_title': '',
                'suggested_source': '',
                'suggested_image': '',
            }
        group['rows'].append(row)
        group['units'] += max(0, row['quantity'])
        if row['location'] and row['location'] not in group['locations']:
            group['locations'].append(row['location'])
    for group in groups.values():
        ages = [row['age_days'] for row in group['rows'] if row['age_days'] is not None]
        group['oldest_age_days'] = max(ages) if ages else None
        group['rows'].sort(key=lambda row: row['id'])
    return groups


# Where a name for a barcode can come from, in the order the add-item gate
# prefers them. Each entry is (source label, database, table, upc column,
# title column, image column).
CATALOG_SOURCES = (
    ('rawbol', 'rawbol.db', 'raw_bol_items', 'upc', 'item_description', 'image_url'),
    ('ebay', 'ebayStore.db', 'INVENTORY', 'UPC', 'Title', 'Image'),
    ('amazon', 'amazonStore.db', 'ITEMS', 'UPC', 'TITLE', 'IMAGE'),
    ('custom_registry', 'bol.db', 'custom_item_registry', 'upc', 'item_description', 'image_url'),
    ('bol', 'bol.db', 'bol_items', 'upc', 'item_description', 'image_url'),
    ('searchrack', 'searchRack.db', 'SEARCHRACK', 'BARCODE', 'TITLE', 'IMAGE'),
)

# SQLite caps bound variables per statement; stay well inside the oldest limit.
_QUERY_CHUNK = 400


def _lookup_candidates(barcode):
    """Codes a catalog might file this barcode under, most exact first."""
    text = str(barcode or '').strip()
    base, _, suffix = text.partition('-')
    stripped = ss_normalization._strip_leading_zeros_numeric(base) or base
    candidates = [text]
    if suffix:
        candidates.append(f'{stripped}-{suffix}')
    candidates.append(base)
    candidates.append(stripped)
    if stripped.isdigit() and len(stripped) <= 13:
        candidates.extend([stripped.zfill(12), stripped.zfill(13)])
    ordered = []
    for candidate in candidates:
        key = str(candidate or '').strip().lower()
        if key and key not in ordered:
            ordered.append(key)
    return ordered


def _catalog_rows(source, wanted):
    """One source's rows for the codes we care about, keyed by lowercased code."""
    _, database, table, upc_col, title_col, image_col = source
    code_expr = f"LOWER(TRIM({ss_database._sqlite_ident(upc_col)}))"
    sql_head = (
        f"SELECT {code_expr} AS code,"
        f" COALESCE({ss_database._sqlite_ident(title_col)}, '') AS title,"
        f" COALESCE({ss_database._sqlite_ident(image_col)}, '') AS image"
        f" FROM {ss_database._sqlite_ident(table)}"
        f" WHERE {code_expr} IN "
    )
    found = {}
    codes = sorted(wanted)
    try:
        with ss_database.db_connection(database) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            for start in range(0, len(codes), _QUERY_CHUNK):
                chunk = codes[start:start + _QUERY_CHUNK]
                placeholders = ','.join('?' for _ in chunk)
                for row in cur.execute(f"{sql_head}({placeholders})", tuple(chunk)):
                    entry = found.setdefault(row['code'], {'title': '', 'image': ''})
                    title = ' '.join(str(row['title'] or '').split())
                    image = str(row['image'] or '').strip()
                    if title and not entry['title'] and not _title_is_meaningless(title):
                        entry['title'] = title
                    if image and not entry['image']:
                        entry['image'] = image
    except sqlite3.Error:
        # A missing table or database only means this source knows nothing.
        pass
    return found


def _resolve_suggestions(groups):
    """Attach the best real name any catalog holds for each barcode."""
    candidates = {group['barcode']: _lookup_candidates(group['barcode']) for group in groups}
    wanted = {code for codes in candidates.values() for code in codes}
    if not wanted:
        return
    per_source = [(source[0], _catalog_rows(source, wanted)) for source in CATALOG_SOURCES]
    for group in groups:
        codes = candidates[group['barcode']]
        for label, rows in per_source:
            for code in codes:
                entry = rows.get(code)
                if not entry:
                    continue
                if entry['title'] and not group['suggested_title']:
                    group['suggested_title'] = entry['title']
                    group['suggested_source'] = label
                if entry['image'] and not group['suggested_image']:
                    group['suggested_image'] = entry['image']
            if group['suggested_title'] and group['suggested_image']:
                break


def api_nameless_items():
    """Split untitled warehouse stock into barcode-only items and nameable ones."""
    try:
        with ss_database.db_connection('searchRack.db') as conn:
            conn.row_factory = sqlite3.Row
            rows = _load_untitled_rows(conn.cursor())

        groups = _group_by_barcode(rows)
        ordered = sorted(groups.values(), key=lambda group: (-(group['oldest_age_days'] or 0), group['barcode']))
        truncated = len(ordered) > NAMELESS_SCAN_LIMIT
        screened = ordered[:NAMELESS_SCAN_LIMIT]

        _resolve_suggestions(screened)
        barcode_only = [group for group in screened if not group['suggested_title']]
        nameable = [group for group in screened if group['suggested_title']]

        def _totals(collection):
            return {
                'groups': len(collection),
                'rows': sum(len(group['rows']) for group in collection),
                'units': sum(group['units'] for group in collection),
            }

        return jsonify({
            'success': True,
            'barcode_only': barcode_only,
            'nameable': nameable,
            'totals': {
                'barcode_only': _totals(barcode_only),
                'nameable': _totals(nameable),
                'scanned_rows': len(rows),
                'scanned_barcodes': len(screened),
            },
            'truncated': truncated,
            'scan_limit': NAMELESS_SCAN_LIMIT,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'warehouse:nameless')}), 500


def _rows_to_name(cur, barcode, requested_ids):
    """Untitled rows for this barcode, restricted to the ids the caller sent."""
    wanted = {ss_normalization._coerce_int(value, 0) for value in (requested_ids or [])}
    wanted.discard(0)
    matches = []
    for row in _load_untitled_rows(cur):
        key = ss_normalization._normalize_scanned_upc(row['barcode']) or row['barcode']
        if key != barcode:
            continue
        if wanted and row['id'] not in wanted:
            continue
        matches.append(row)
    return matches


def api_name_nameless_item():
    """Give a barcode-only warehouse item a real name, on the rows and for later scans."""
    try:
        data = request.get_json(silent=True) or {}
        barcode = ss_normalization._normalize_scanned_upc(data.get('barcode'))
        title = ' '.join(str(data.get('title') or data.get('item_description') or '').split())
        rejection = _manual_title_rejection(title)
        if rejection:
            return jsonify({'success': False, 'error': rejection}), 400
        if not barcode or not title:
            return jsonify({'success': False, 'error': 'Barcode and title are required'}), 400

        with ss_database.db_connection('searchRack.db') as conn:
            conn.row_factory = sqlite3.Row
            targets = _rows_to_name(conn.cursor(), barcode, data.get('ids'))
        if not targets:
            return jsonify({'success': False, 'error': 'No unnamed warehouse rows left for this barcode'}), 404

        # The product identity lives on the base UPC, so every suffixed unit of
        # the same item inherits the name on its next scan. Register it before
        # touching the rows: the registry's one-time backfill of custom-titled
        # rack rows must not see this write and file it under the suffix too.
        registry_key = ss_normalization._normalize_upc(_base_barcode(barcode)) or barcode
        with ss_database.db_connection('bol.db') as conn:
            cur = conn.cursor()
            ss_warehouse_receiving._ensure_custom_item_registry(cur)
            cur.execute('''
                INSERT INTO custom_item_registry (
                    upc, item_description, title_override, reserved_at, updated_at
                ) VALUES (?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(upc) DO UPDATE SET
                    item_description = excluded.item_description,
                    title_override = 1,
                    updated_at = CURRENT_TIMESTAMP
            ''', (registry_key, title))

        with ss_database.db_connection('searchRack.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            assignments = ['TITLE = ?']
            if 'custom_title' in _searchrack_columns(cur):
                assignments.append('CUSTOM_TITLE = 1')
            for row in targets:
                cur.execute(
                    f"UPDATE SEARCHRACK SET {', '.join(assignments)} WHERE ID = ?",
                    (title, row['id']),
                )

        ss_caching._invalidate_searchrack_cache()
        return jsonify({
            'success': True,
            'barcode': barcode,
            'title': title,
            'named_ids': [row['id'] for row in targets],
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'warehouse:nameless:name')}), 500


def nameless_items_page():
    """Warehouse rows holding nothing but a barcode, with a name box on each."""
    return render_template('no_name_items.html')
