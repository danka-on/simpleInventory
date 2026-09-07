"""Warehouse matching for Sweet Shelves."""

import datetime
import re
import sqlite3
from DBmanager import resolve_barcode_from_marketplace_sku
from . import (
    caching as ss_caching, database as ss_database, errors as ss_errors, inventory_age as
    ss_inventory_age, inventory_history as ss_inventory_history, listing_alerts as ss_listing_alerts,
    normalization as ss_normalization, shipping_identity as ss_shipping_identity,
)


def _sold_location_key(value):
    s = str(value or '').strip()
    if not s:
        return '__no_code__'
    return s.lower()


def _sold_location_label(value):
    s = str(value or '').strip()
    return s if s else '(No Code)'


def _sold_removal_barcode_key(value):
    return ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(value))


def _sold_removal_barcode_variants(value):
    raw = ss_normalization._normalize_upc(value)
    key = _sold_removal_barcode_key(raw)
    variants = set()

    for candidate in (raw, key):
        s = str(candidate or '').strip()
        if s:
            variants.add(s)

    if key and '-' in key:
        base, suffix = key.split('-', 1)
        if base.isdigit():
            for width in (8, 11, 12, 13, 14):
                if len(base) < width:
                    variants.add(f"{base.zfill(width)}-{suffix}")
            # Include base-only variants: suffixed store SKUs (e.g. "035886267162-1")
            # must still match inventory stored under the plain base barcode.
            stripped_base = base.lstrip('0') or base
            variants.add(stripped_base)
            for width in (8, 11, 12, 13, 14):
                if len(stripped_base) < width:
                    variants.add(stripped_base.zfill(width))
    elif key and key.isdigit():
        for width in (8, 11, 12, 13, 14):
            if len(key) < width:
                variants.add(key.zfill(width))

    if raw and raw.isdigit():
        stripped = raw.lstrip('0') or '0'
        variants.add(stripped)
        for width in (8, 11, 12, 13, 14):
            if len(stripped) < width:
                variants.add(stripped.zfill(width))

    return {str(v).strip() for v in variants if str(v).strip()}


def _searchrack_removal_schema(cur):
    cur.execute("PRAGMA table_info('SEARCHRACK')")
    cols = [r[1] for r in cur.fetchall()]
    cols_lower = {c.lower(): c for c in cols}
    return {
        'id_col': cols_lower.get('id'),
        'barcode_col': cols_lower.get('barcode') or cols_lower.get('upc'),
        'qty_col': cols_lower.get('quantity') or cols_lower.get('qty'),
        'title_col': cols_lower.get('title'),
        'image_col': cols_lower.get('image') or cols_lower.get('images'),
        'custom_title_col': cols_lower.get('custom_title'),
        'pos_col': cols_lower.get('item_position') or cols_lower.get('itemposition') or cols_lower.get('position'),
        'pic_col': cols_lower.get('pictureposition'),
        'created_col': cols_lower.get('created_at')
    }


def _searchrack_matches_for_barcode(cur, barcode, schema=None, include_zero=False):
    schema = schema or _searchrack_removal_schema(cur)
    barcode_col = schema.get('barcode_col')
    qty_col = schema.get('qty_col')
    id_col = schema.get('id_col')
    title_col = schema.get('title_col')
    image_col = schema.get('image_col')
    custom_title_col = schema.get('custom_title_col')
    pos_col = schema.get('pos_col')
    pic_col = schema.get('pic_col')

    target_key = _sold_removal_barcode_key(barcode)
    if not target_key or not barcode_col:
        return []

    base_sql = f'''
        SELECT rowid AS _rowid_, *
        FROM SEARCHRACK
        WHERE {barcode_col} IS NOT NULL
          AND TRIM({barcode_col}) != ''
    '''

    variants = sorted(v.lower() for v in _sold_removal_barcode_variants(barcode))
    rows = []
    if variants:
        placeholders = ','.join('?' for _ in variants)
        cur.execute(base_sql + f" AND LOWER(TRIM({barcode_col})) IN ({placeholders})", tuple(variants))
        rows = [dict(r) for r in cur.fetchall()]
    else:
        cur.execute(base_sql)
        rows = [dict(r) for r in cur.fetchall()]

    # When the target barcode has a suffix (e.g. "35886267162-1"), also accept rows
    # whose stored barcode is the plain base (e.g. "035886267162") — covers the case
    # where eBay/Amazon SKUs are suffixed but inventory was scanned under the base UPC.
    _target_base_key = target_key.split('-', 1)[0] if target_key and '-' in target_key else None

    exact_matches = []
    exact_seen = False
    fallback_matches = []
    for row in rows:
        raw_barcode = str(row.get(barcode_col) or '').strip()
        row_key = _sold_removal_barcode_key(raw_barcode)
        is_exact = row_key == target_key
        is_base_fallback = bool(_target_base_key and row_key == _target_base_key)
        if not is_exact and not is_base_fallback:
            continue
        if is_exact:
            exact_seen = True

        qty = max(0, ss_normalization._coerce_int(row.get(qty_col) if qty_col else None, 0))
        if not include_zero and qty <= 0:
            continue

        raw_id = row.get(id_col) if id_col else row.get('_rowid_')
        try:
            row_id = int(raw_id)
        except Exception:
            continue

        item_position = str(row.get(pos_col) or '').strip() if pos_col else ''
        pictureposition = str(row.get(pic_col) or '').strip() if pic_col else ''
        location_code = item_position or pictureposition
        location_label = _sold_location_label(location_code)

        match = {
            'id': row_id,
            'barcode': raw_barcode,
            'title': str(row.get(title_col) or '').strip() if title_col else '',
            'image': str(row.get(image_col) or '').strip() if image_col else '',
            'custom_title': bool(ss_normalization._coerce_int(row.get(custom_title_col), 0)) if custom_title_col else False,
            'quantity': qty,
            'item_position': item_position,
            'pictureposition': pictureposition,
            'warehouse_note': str(row.get('WAREHOUSE_NOTE') or row.get('warehouse_note') or '').strip(),
            'location_code': location_label,
            'location_key': _sold_location_key(location_label),
            'location_preview': pictureposition or item_position or location_label
        }
        if is_exact:
            exact_matches.append(match)
        else:
            fallback_matches.append(match)

    matches = exact_matches if exact_seen else fallback_matches
    matches.sort(key=lambda m: (m.get('location_key') or '', int(m.get('id') or 0)))
    return matches


def _ready_to_ship_suffix_inventory_matches(cur, barcode, schema=None):
    """Return available SEARCHRACK rows whose barcode is a suffix variant."""
    schema = schema or _searchrack_removal_schema(cur)
    barcode_col = schema.get('barcode_col')
    base_barcode = ss_shipping_identity._barcode_base_without_suffix(barcode)
    target_base_key = _sold_removal_barcode_key(base_barcode).split('-', 1)[0]
    if not barcode_col or not target_base_key:
        return []

    base_variants = {
        str(value or '').strip().lower()
        for value in _sold_removal_barcode_variants(base_barcode)
        if value and '-' not in str(value)
    }
    if not base_variants:
        return []

    where_bits = [f"LOWER(TRIM({barcode_col})) LIKE ?" for _ in base_variants]
    params = tuple(f"{value}-%" for value in sorted(base_variants))
    cur.execute(f'''
        SELECT DISTINCT TRIM({barcode_col}) AS barcode
        FROM SEARCHRACK
        WHERE {barcode_col} IS NOT NULL
          AND ({' OR '.join(where_bits)})
    ''', params)

    matches = []
    seen_ids = set()
    for row in cur.fetchall():
        candidate_barcode = str(row['barcode'] or '').strip()
        candidate_base_key = _sold_removal_barcode_key(
            ss_shipping_identity._barcode_base_without_suffix(candidate_barcode)
        ).split('-', 1)[0]
        if candidate_base_key != target_base_key:
            continue
        for match in _searchrack_matches_for_barcode(
            cur, candidate_barcode, schema=schema, include_zero=False
        ):
            match_id = ss_normalization._coerce_int(match.get('id'), 0)
            if match_id and match_id not in seen_ids:
                seen_ids.add(match_id)
                matches.append(match)

    matches.sort(key=lambda match: (
        _sold_removal_barcode_key(match.get('barcode')),
        match.get('location_key') or '',
        ss_normalization._coerce_int(match.get('id'), 0)
    ))
    return matches


def _ready_to_ship_condition_is_new(order):
    raw = str((order or {}).get('item_condition') or (order or {}).get('condition') or '').strip()
    normalized = re.sub(r'[^a-z0-9]+', '_', raw.lower()).strip('_')
    return normalized in {'11', '1000', 'new_new', 'new', 'brand_new'}


def _ready_to_ship_valid_suffix_suggestion(order_barcode, suggested_barcode):
    order_key = _sold_removal_barcode_key(order_barcode)
    suggested_key = _sold_removal_barcode_key(suggested_barcode)
    if not order_key or not suggested_key or '-' not in suggested_key:
        return False
    return order_key.split('-', 1)[0] == suggested_key.split('-', 1)[0]


def _ready_to_ship_valid_requested_match(order, suggested_barcode):
    suggested = str(suggested_barcode or '').strip()
    order_barcode = _effective_sold_order_barcode(order, prefer_manual_override=True)
    if _ready_to_ship_valid_suffix_suggestion(order_barcode, suggested):
        return True
    mapping = ss_listing_alerts._listing_inventory_match_for_order(order)
    mapped_barcode = str((mapping or {}).get('inventory_barcode') or '').strip()
    return bool(
        suggested
        and mapped_barcode
        and _sold_removal_barcode_key(suggested) == _sold_removal_barcode_key(mapped_barcode)
    )


def _ready_to_ship_suffix_alternatives(matches, selected_barcode=''):
    selected_key = _sold_removal_barcode_key(selected_barcode)
    grouped = {}
    for match in matches or []:
        barcode = str((match or {}).get('barcode') or '').strip()
        barcode_key = _sold_removal_barcode_key(barcode)
        if not barcode_key or barcode_key == selected_key:
            continue
        bucket = grouped.setdefault(barcode_key, {
            'barcode': barcode,
            'quantity': 0,
            'locations': []
        })
        bucket['quantity'] += max(0, ss_normalization._coerce_int((match or {}).get('quantity'), 0))
        location = _ready_to_ship_match_display_location(match)
        if location and location not in bucket['locations']:
            bucket['locations'].append(location)
    return sorted(grouped.values(), key=lambda item: _sold_removal_barcode_key(item.get('barcode')))


def _searchrack_matches_for_removal_context(cur, barcode, *, fallback_barcodes=None, preferred_location=''):
    """Find removable inventory using the same fallbacks as Ready to Ship location lookup."""
    schema = _searchrack_removal_schema(cur)
    seen_barcode_keys = set()

    def _seen_key(value):
        return str(ss_normalization._normalize_upc_preserve_suffix_for_match(value) or '').strip().lower()

    def _try_barcode(value):
        raw = str(value or '').strip()
        key = _seen_key(raw)
        if not raw or not key or key in seen_barcode_keys:
            return []
        seen_barcode_keys.add(key)

        found = _searchrack_matches_for_barcode(cur, raw, schema=schema, include_zero=False)
        if found:
            return found

        base_barcode = ss_shipping_identity._barcode_base_without_suffix(raw)
        base_key = _seen_key(base_barcode)
        if base_barcode and base_key and base_key != key and base_key not in seen_barcode_keys:
            seen_barcode_keys.add(base_key)
            return _searchrack_matches_for_barcode(cur, base_barcode, schema=schema, include_zero=False)
        return []

    candidates = [barcode]
    for fallback in (fallback_barcodes or []):
        if str(fallback or '').strip():
            candidates.append(fallback)

    for candidate in candidates:
        matches = _try_barcode(candidate)
        if matches:
            return matches

    location = str(preferred_location or '').strip()
    qty_col = schema.get('qty_col')
    barcode_col = schema.get('barcode_col')
    pos_col = schema.get('pos_col')
    pic_col = schema.get('pic_col')
    location_cols = []
    for col in (pos_col, pic_col):
        if col and col not in location_cols:
            location_cols.append(col)
    if not location or not qty_col or not barcode_col or not location_cols:
        return []

    try:
        where_bits = [f"LOWER(TRIM(COALESCE({col}, ''))) = LOWER(TRIM(?))" for col in location_cols]
        cur.execute(
            f"""
            SELECT rowid AS _rowid_, *
            FROM SEARCHRACK
            WHERE ({' OR '.join(where_bits)})
              AND COALESCE({qty_col}, 0) > 0
            """,
            tuple(location for _ in location_cols)
        )
        location_rows = cur.fetchall()
    except Exception:
        return []

    if not location_rows:
        return []

    order_variant_keys = set()
    for candidate in candidates:
        order_variant_keys.update(
            _sold_removal_barcode_key(v)
            for v in _sold_removal_barcode_variants(candidate)
            if str(v or '').strip()
        )
    order_variant_keys.discard('')

    chosen_barcode = ''
    for row in location_rows:
        row_dict = dict(row)
        row_barcode = str(row_dict.get(barcode_col) or '').strip()
        if row_barcode and _sold_removal_barcode_key(row_barcode) in order_variant_keys:
            chosen_barcode = row_barcode
            break
    if not chosen_barcode:
        for row in location_rows:
            row_dict = dict(row)
            row_barcode = str(row_dict.get(barcode_col) or '').strip()
            if row_barcode:
                chosen_barcode = row_barcode
                break

    if not chosen_barcode:
        return []

    found = _searchrack_matches_for_barcode(cur, chosen_barcode, schema=schema, include_zero=False)
    if found:
        return found

    base_barcode = ss_shipping_identity._barcode_base_without_suffix(chosen_barcode)
    if base_barcode and _seen_key(base_barcode) != _seen_key(chosen_barcode):
        return _searchrack_matches_for_barcode(cur, base_barcode, schema=schema, include_zero=False)
    return []


def _ready_to_ship_searchrack_match_from_row(row, schema):
    row_dict = dict(row)
    id_col = schema.get('id_col')
    barcode_col = schema.get('barcode_col')
    qty_col = schema.get('qty_col')
    title_col = schema.get('title_col')
    image_col = schema.get('image_col')
    custom_title_col = schema.get('custom_title_col')
    pos_col = schema.get('pos_col')
    pic_col = schema.get('pic_col')
    note_col = None
    for candidate in ('WAREHOUSE_NOTE', 'warehouse_note'):
        if candidate in row_dict:
            note_col = candidate
            break

    raw_id = row_dict.get(id_col) if id_col else row_dict.get('_rowid_')
    try:
        row_id = int(raw_id)
    except Exception:
        return None

    item_position = str(row_dict.get(pos_col) or '').strip() if pos_col else ''
    pictureposition = str(row_dict.get(pic_col) or '').strip() if pic_col else ''
    location_code = item_position or pictureposition
    location_label = _sold_location_label(location_code)

    return {
        'id': row_id,
        'barcode': str(row_dict.get(barcode_col) or '').strip() if barcode_col else '',
        'title': str(row_dict.get(title_col) or '').strip() if title_col else '',
        'image': str(row_dict.get(image_col) or '').strip() if image_col else '',
        'custom_title': bool(ss_normalization._coerce_int(row_dict.get(custom_title_col), 0)) if custom_title_col else False,
        'quantity': max(0, ss_normalization._coerce_int(row_dict.get(qty_col) if qty_col else None, 0)),
        'item_position': item_position,
        'pictureposition': pictureposition,
        'warehouse_note': str(row_dict.get(note_col) or '').strip() if note_col else '',
        'location_code': location_label,
        'location_key': _sold_location_key(location_label),
        'location_preview': pictureposition or item_position or location_label
    }


def _ready_to_ship_rank_inventory_matches(matches, order):
    """Prefer the same-barcode warehouse row whose custom identity matches the sold item."""
    order_title = ' '.join(str((order or {}).get('title') or '').casefold().split())
    order_image = str((order or {}).get('image') or (order or {}).get('image_url') or '').strip().casefold()

    def _image_key(value):
        raw = str(value or '').strip().casefold().split('?', 1)[0].rstrip('/')
        return raw.rsplit('/', 1)[-1] if raw else ''

    order_image_key = _image_key(order_image)

    def _score(match):
        match_title = ' '.join(str((match or {}).get('title') or '').casefold().split())
        match_image = str((match or {}).get('image') or '').strip().casefold()
        match_image_key = _image_key(match_image)
        title_exact = bool(order_title and match_title and order_title == match_title)
        image_exact = bool(
            order_image and match_image
            and (order_image == match_image or (order_image_key and order_image_key == match_image_key))
        )
        custom = bool((match or {}).get('custom_title'))
        return (
            1 if title_exact and image_exact else 0,
            1 if title_exact else 0,
            1 if image_exact else 0,
            1 if custom and (title_exact or image_exact) else 0,
            1 if custom else 0,
            max(0, ss_normalization._coerce_int((match or {}).get('id'), 0)),
        )

    return sorted(list(matches or []), key=_score, reverse=True)


def _ready_to_ship_match_display_location(match):
    item_position = str((match or {}).get('item_position') or '').strip()
    pictureposition = str((match or {}).get('pictureposition') or '').strip()
    if pictureposition and item_position.lower() in ('', 'picture'):
        return pictureposition
    return item_position or pictureposition or str((match or {}).get('location_code') or '').strip()


def _ready_to_ship_searchrack_match_by_id(cur, searchrack_id, schema=None):
    try:
        row_id = int(searchrack_id)
    except Exception:
        return None
    if row_id <= 0:
        return None

    schema = schema or _searchrack_removal_schema(cur)
    id_col = schema.get('id_col')

    def _ident(name):
        return '"' + str(name).replace('"', '""') + '"'

    lookup_expr = _ident(id_col) if id_col else 'rowid'

    try:
        cur.execute(
            f"SELECT rowid AS _rowid_, * FROM SEARCHRACK WHERE {lookup_expr} = ? LIMIT 1",
            (row_id,)
        )
        row = cur.fetchone()
    except Exception:
        return None
    if not row:
        return None
    match = _ready_to_ship_searchrack_match_from_row(row, schema)
    if not match or max(0, ss_normalization._coerce_int(match.get('quantity'), 0)) <= 0:
        return None
    return match


def _ready_to_ship_match_location_equals(match, location):
    wanted = _sold_location_key(location)
    if not location or wanted == '__no_code__':
        return False
    for key in ('location_key', 'location_code', 'item_position', 'pictureposition', 'location_preview'):
        value = match.get(key)
        if key == 'location_key':
            if str(value or '').strip().lower() == wanted:
                return True
        elif _sold_location_key(value) == wanted:
            return True
    return False


def _ready_to_ship_resolve_searchrack_location_match(cur, barcode, location, fallback_barcodes=None):
    """Best-effort resolver for older Finder locks that predate stored SEARCHRACK row ids."""
    location = str(location or '').strip()
    schema = _searchrack_removal_schema(cur)
    candidates = []
    for value in [barcode] + list(fallback_barcodes or []):
        raw = str(value or '').strip()
        if raw:
            candidates.append(raw)

    candidate_keys = set()
    seen_candidate_keys = set()
    for candidate in candidates:
        candidate_key = str(ss_normalization._normalize_upc_preserve_suffix_for_match(candidate) or '').strip().lower()
        if not candidate_key or candidate_key in seen_candidate_keys:
            continue
        seen_candidate_keys.add(candidate_key)
        for variant in _sold_removal_barcode_variants(candidate):
            variant_key = _sold_removal_barcode_key(variant)
            if variant_key:
                candidate_keys.add(variant_key)

        matches = _searchrack_matches_for_barcode(cur, candidate, schema=schema, include_zero=False)
        if location:
            exact = next((m for m in matches if _ready_to_ship_match_location_equals(m, location)), None)
            if exact:
                return exact
        elif len(matches) == 1:
            return matches[0]

        base_barcode = ss_shipping_identity._barcode_base_without_suffix(candidate)
        base_key = str(ss_normalization._normalize_upc_preserve_suffix_for_match(base_barcode) or '').strip().lower()
        if base_barcode and base_key and base_key != candidate_key and base_key not in seen_candidate_keys:
            seen_candidate_keys.add(base_key)
            base_matches = _searchrack_matches_for_barcode(cur, base_barcode, schema=schema, include_zero=False)
            if location:
                exact = next((m for m in base_matches if _ready_to_ship_match_location_equals(m, location)), None)
                if exact:
                    return exact
            elif len(base_matches) == 1:
                return base_matches[0]

    if not location:
        return None

    qty_col = schema.get('qty_col')
    barcode_col = schema.get('barcode_col')
    pos_col = schema.get('pos_col')
    pic_col = schema.get('pic_col')
    location_cols = []
    for col in (pos_col, pic_col):
        if col and col not in location_cols:
            location_cols.append(col)
    if not location_cols:
        return None

    def _ident(name):
        return '"' + str(name).replace('"', '""') + '"'

    try:
        where_bits = [f"LOWER(TRIM(COALESCE({_ident(col)}, ''))) = LOWER(TRIM(?))" for col in location_cols]
        cur.execute(
            f"""
            SELECT rowid AS _rowid_, *
            FROM SEARCHRACK
            WHERE {' OR '.join(where_bits)}
            """,
            tuple(location for _ in location_cols)
        )
        location_rows = cur.fetchall()
    except Exception:
        return None

    matches = []
    for row in location_rows:
        match = _ready_to_ship_searchrack_match_from_row(row, schema)
        if match:
            matches.append(match)

    if not matches:
        return None

    if candidate_keys and barcode_col:
        exact_barcode = next(
            (m for m in matches if _sold_removal_barcode_key(m.get('barcode')) in candidate_keys),
            None
        )
        if exact_barcode:
            return exact_barcode

    if len(matches) == 1:
        return matches[0]
    return None


def _group_searchrack_matches_by_location(matches):
    groups = {}
    for row in (matches or []):
        loc_key = _sold_location_key(row.get('location_key') or row.get('location_code'))
        bucket = groups.get(loc_key)
        if bucket is None:
            bucket = {
                'location_key': loc_key,
                'location_code': _sold_location_label(row.get('location_code')),
                'preview_key': str(row.get('location_preview') or row.get('location_code') or '').strip(),
                'available_qty': 0,
                'rows': []
            }
            groups[loc_key] = bucket
        bucket['available_qty'] += max(0, ss_normalization._coerce_int(row.get('quantity'), 0))
        bucket['rows'].append(row)

    out = []
    for bucket in groups.values():
        bucket['rows'].sort(key=lambda r: int(r.get('id') or 0))
        out.append(bucket)
    out.sort(key=lambda g: str(g.get('location_code') or '').lower())
    return out


def _serialize_prep_location_entry(group):
    """Convert a grouped SEARCHRACK location bucket into a lightweight API payload."""
    group = group or {}
    rows = list(group.get('rows') or [])
    first_row = rows[0] if rows else {}
    clear_target_row_id = None
    for row in rows:
        try:
            clear_target_row_id = int(row.get('id'))
            break
        except Exception:
            continue

    location_code = _sold_location_label(group.get('location_code') or first_row.get('location_code') or '')
    preview_key = str(
        group.get('preview_key')
        or first_row.get('location_preview')
        or first_row.get('pictureposition')
        or first_row.get('item_position')
        or location_code
        or ''
    ).strip()

    return {
        'location_key': str(group.get('location_key') or _sold_location_key(location_code)).strip(),
        'location_code': location_code,
        'preview_key': preview_key,
        'available_qty': max(0, ss_normalization._coerce_int(group.get('available_qty'), 0)),
        'clear_target_row_id': clear_target_row_id,
        'clearable': clear_target_row_id is not None
    }


def _prep_history_inventory_fallback(base_upc):
    """Collect live inventory rows for a base UPC so prep history can fall back to warehouse locations."""
    base_key = _sold_removal_barcode_key(base_upc)
    if not base_key:
        return {}

    out = {}
    try:
        with ss_database.db_connection('searchRack.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            schema = _searchrack_removal_schema(cur)
            id_col = schema.get('id_col')
            barcode_col = schema.get('barcode_col')
            qty_col = schema.get('qty_col')
            title_col = schema.get('title_col') or "''"
            pos_col = schema.get('pos_col') or "''"
            pic_col = schema.get('pic_col') or "''"
            created_col = schema.get('created_col') or "''"
            if not barcode_col or not qty_col:
                return {}
            id_expr = id_col or 'rowid'

            variants = sorted(str(v).strip().lower() for v in _sold_removal_barcode_variants(base_key) if str(v).strip())
            if not variants:
                return {}

            exact_clause = f"LOWER(TRIM({barcode_col})) IN ({','.join('?' for _ in variants)})"
            like_patterns = []
            for variant in variants:
                safe_variant = variant.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                like_patterns.append(f'{safe_variant}-%')
            like_clause = ' OR '.join(
                f"LOWER(TRIM({barcode_col})) LIKE ? ESCAPE '\\'" for _ in like_patterns
            )

            sql = f'''
                SELECT COALESCE({id_expr}, rowid) AS searchrack_id,
                       COALESCE({barcode_col}, '') AS barcode,
                       COALESCE({title_col}, '') AS title,
                       COALESCE({pos_col}, '') AS item_position,
                       COALESCE({pic_col}, '') AS pictureposition,
                       COALESCE({qty_col}, 0) AS quantity,
                       COALESCE({created_col}, '') AS created_at
                FROM SEARCHRACK
                WHERE ({exact_clause}{(' OR ' + like_clause) if like_clause else ''})
                  AND COALESCE({qty_col}, 0) > 0
            '''
            params = list(variants) + like_patterns
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
    except Exception:
        return {}

    for row in rows:
        raw_barcode = str(row['barcode'] or '').strip()
        barcode_key = _sold_removal_barcode_key(raw_barcode)
        if not raw_barcode or not barcode_key:
            continue

        qty = max(0, ss_normalization._coerce_int(row['quantity'], 0))
        if qty <= 0:
            continue
        row_id = max(0, ss_normalization._coerce_int(row['searchrack_id'], 0))

        bucket = out.get(barcode_key)
        if bucket is None:
            bucket = {
                'upc': raw_barcode,
                'title': str(row['title'] or '').strip(),
                'updated_at': str(row['created_at'] or '').strip(),
                'quantity': 0,
                'row_ids': [],
                'locations': {}
            }
            out[barcode_key] = bucket
        elif not bucket.get('title'):
            bucket['title'] = str(row['title'] or '').strip()

        created_at = str(row['created_at'] or '').strip()
        if created_at and (not bucket.get('updated_at') or created_at > bucket['updated_at']):
            bucket['updated_at'] = created_at
        bucket['quantity'] += qty
        if row_id > 0 and row_id not in bucket['row_ids']:
            bucket['row_ids'].append(row_id)

        item_position = str(row['item_position'] or '').strip()
        pictureposition = str(row['pictureposition'] or '').strip()
        location_raw = item_position or pictureposition
        if not location_raw:
            continue

        location_label = _sold_location_label(location_raw)
        location_key = _sold_location_key(location_label)
        location_bucket = bucket['locations'].get(location_key)
        if location_bucket is None:
            location_bucket = {
                'location_code': location_label,
                'preview_key': pictureposition or item_position or location_label,
                'available_qty': 0,
                'row_ids': [],
                'clear_target_row_id': row_id or None,
                'clearable': row_id > 0
            }
            bucket['locations'][location_key] = location_bucket
        location_bucket['available_qty'] += qty
        if row_id > 0 and row_id not in location_bucket['row_ids']:
            location_bucket['row_ids'].append(row_id)
        if row_id > 0 and not location_bucket.get('clear_target_row_id'):
            location_bucket['clear_target_row_id'] = row_id
            location_bucket['clearable'] = True

    final = {}
    for barcode_key, bucket in out.items():
        locations = list(bucket.get('locations', {}).values())
        locations.sort(key=lambda loc: str(loc.get('location_code') or '').lower())
        final[barcode_key] = {
            'upc': bucket.get('upc') or '',
            'title': bucket.get('title') or '',
            'updated_at': bucket.get('updated_at') or '',
            'quantity': max(0, ss_normalization._coerce_int(bucket.get('quantity'), 0)),
            'row_ids': list(bucket.get('row_ids') or []),
            'locations': locations
        }
    return final


def _prep_history_inventory_bucket_for_upc(upc):
    """Resolve the live-inventory fallback bucket for this exact UPC key only."""
    upc_key = _sold_removal_barcode_key(upc)
    if not upc_key:
        return {}
    inventory_map = _prep_history_inventory_fallback(upc)
    return inventory_map.get(upc_key) or {}


def _prep_select_inventory_match(upc, preferred_location='', preferred_preview=''):
    """Pick a single live inventory row for a UPC, preferring an explicit location/picture match."""
    try:
        with ss_database.db_connection('searchRack.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            schema = _searchrack_removal_schema(cur)
            matches = _searchrack_matches_for_barcode(cur, upc, schema=schema, include_zero=False)
    except Exception:
        return {'match': None, 'location_groups': [], 'clearable': False, 'ambiguous': False}

    target_key = _sold_removal_barcode_key(upc)
    if target_key:
        matches = [
            row for row in matches
            if _sold_removal_barcode_key(row.get('barcode')) == target_key
        ]

    location_groups = _group_searchrack_matches_by_location(matches)
    pref_values = {
        str(v or '').strip().lower()
        for v in (preferred_location, preferred_preview)
        if str(v or '').strip()
    }

    def _row_matches_pref(row):
        if not pref_values:
            return False
        candidates = {
            str(row.get('location_key') or '').strip().lower(),
            str(row.get('location_code') or '').strip().lower(),
            str(row.get('item_position') or '').strip().lower(),
            str(row.get('pictureposition') or '').strip().lower(),
            str(row.get('location_preview') or '').strip().lower()
        }
        candidates.discard('')
        return bool(candidates & pref_values)

    preferred_matches = [row for row in matches if _row_matches_pref(row)]
    if preferred_matches:
        preferred_matches.sort(key=lambda r: (str(r.get('location_key') or ''), int(r.get('id') or 0)))
        return {
            'match': preferred_matches[0],
            'location_groups': location_groups,
            'clearable': True,
            'ambiguous': False
        }

    if len(matches) == 1 or len(location_groups) == 1:
        chosen = matches[0] if matches else None
        return {
            'match': chosen,
            'location_groups': location_groups,
            'clearable': bool(chosen),
            'ambiguous': False
        }

    return {
        'match': None,
        'location_groups': location_groups,
        'clearable': False,
        'ambiguous': len(location_groups) > 1
    }


def _clear_searchrack_row_location(row_id):
    """Remove exactly one unit from the selected SEARCHRACK row/location."""
    if row_id is None or str(row_id).strip() == '':
        return {'success': False, 'error': 'Missing inventory row id'}

    conn = None
    rem_conn = None
    try:
        conn = sqlite3.connect('searchRack.db')
        conn.row_factory = sqlite3.Row
        ss_inventory_age._reconcile_inventory_age_batches(conn)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info('SEARCHRACK')")
        cols = [r[1] for r in cur.fetchall()]
        cols_lower = {c.lower(): c for c in cols}
        id_col = cols_lower.get('id')
        id_lookup_col = id_col or 'rowid'
        barcode_col = cols_lower.get('barcode') or cols_lower.get('upc')
        title_col = cols_lower.get('title')
        qty_col = cols_lower.get('quantity') or cols_lower.get('qty')
        pos_col = cols_lower.get('item_position') or cols_lower.get('itemposition') or cols_lower.get('position')
        pic_col = cols_lower.get('pictureposition')
        if not qty_col:
            return {'success': False, 'error': 'Inventory quantity column not found'}

        cur.execute(f"SELECT rowid AS _rowid_, * FROM SEARCHRACK WHERE {id_lookup_col} = ?", (row_id,))
        existing = cur.fetchone()
        if not existing:
            return {'success': False, 'error': 'Inventory row not found'}

        row = dict(existing)
        barcode = str(row.get(barcode_col) or '').strip() if barcode_col else ''
        title = str(row.get(title_col) or '').strip() if title_col else ''
        current_qty = max(0, ss_normalization._coerce_int(row.get(qty_col) if qty_col else None, 1))
        if current_qty <= 0:
            return {'success': False, 'error': 'Inventory row has no quantity'}

        item_position = str(row.get(pos_col) or '').strip() if pos_col else ''
        pictureposition = str(row.get(pic_col) or '').strip() if pic_col else ''
        source_location = pictureposition if (pictureposition and item_position.lower() in ('', 'picture')) else (item_position or pictureposition)
        if not source_location:
            return {'success': True, 'row_id': int(row_id), 'source_location': '', 'quantity_cleared': 0}

        rem_conn = sqlite3.connect('rackhistory.db')
        rem_cur = rem_conn.cursor()
        ss_inventory_history._ensure_removed_items_table(rem_cur)
        try:
            rem_cur.execute("PRAGMA table_info(removed_items)")
            rem_cols = [c[1] for c in rem_cur.fetchall()]
            if 'item_position' not in rem_cols:
                rem_cur.execute('ALTER TABLE removed_items ADD COLUMN item_position TEXT')
        except Exception:
            pass

        now = datetime.datetime.now().isoformat()
        new_qty = max(0, current_qty - 1)
        if qty_col:
            cur.execute(
                f"UPDATE SEARCHRACK SET {qty_col} = ? WHERE {id_lookup_col} = ?",
                (new_qty, row_id)
            )
        ss_inventory_age._inventory_age_consume_fifo(cur, row_id, 1)

        rem_cur.execute('''
            INSERT INTO removed_items
            (order_id, barcode, title, quantity_removed, removed_at, searchrack_id,
             old_quantity, new_quantity, removal_type, item_position, event_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        ''', (None, barcode, title, 1, now, row_id, current_qty, new_qty, 'locationcleared', source_location))

        rem_conn.commit()
        conn.commit()
        try:
            ss_caching._invalidate_searchrack_cache()
            ss_caching.update_data_version()
        except Exception:
            pass
        return {'success': True, 'row_id': int(row_id), 'source_location': source_location, 'quantity_cleared': 1}
    except Exception as e:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
        try:
            if rem_conn is not None:
                rem_conn.rollback()
        except Exception:
            pass
        return {'success': False, 'error': ss_errors._safe_error(e)}
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
        try:
            if rem_conn is not None:
                rem_conn.close()
        except Exception:
            pass


def _items_prep_delete_legacy_inventory_entry(*, upc, qty=1, row_ids=None):
    """Remove a live SEARCHRACK fallback row shown as an older prep-history entry."""
    target_key = _sold_removal_barcode_key(upc)
    if not target_key:
        return {'success': False, 'error': 'Missing upc', 'status_code': 400}

    qty_n = ss_normalization._strict_inventory_quantity(qty if qty is not None else 1)
    if qty_n is None or qty_n < 1:
        return {'success': False, 'error': 'Quantity must be a positive whole number', 'status_code': 400}

    raw_ids = row_ids if isinstance(row_ids, (list, tuple)) else ([row_ids] if row_ids not in (None, '') else [])
    wanted_ids = []
    for raw_id in raw_ids:
        try:
            row_id = int(raw_id)
        except Exception:
            continue
        if row_id > 0 and row_id not in wanted_ids:
            wanted_ids.append(row_id)

    def _ident(name):
        return '"' + str(name).replace('"', '""') + '"'

    rack_conn = None
    rem_conn = None
    zero_qty_row_ids = []
    try:
        rack_conn = sqlite3.connect('searchRack.db', isolation_level='IMMEDIATE')
        rack_conn.row_factory = sqlite3.Row
        rack_cur = rack_conn.cursor()
        schema = _searchrack_removal_schema(rack_cur)
        id_col = schema.get('id_col')
        qty_col = schema.get('qty_col')
        if not qty_col:
            return {'success': False, 'error': 'Inventory quantity column not found', 'status_code': 500}

        id_lookup_expr = _ident(id_col) if id_col else 'rowid'
        qty_expr = _ident(qty_col)
        candidates = []

        if wanted_ids:
            placeholders = ','.join('?' for _ in wanted_ids)
            rack_cur.execute(
                f"SELECT rowid AS _rowid_, * FROM SEARCHRACK WHERE {id_lookup_expr} IN ({placeholders})",
                tuple(wanted_ids)
            )
            for row in rack_cur.fetchall():
                match = _ready_to_ship_searchrack_match_from_row(row, schema)
                if match:
                    candidates.append(match)

        if not candidates:
            candidates = _searchrack_matches_for_barcode(rack_cur, upc, schema=schema, include_zero=False)

        matches = []
        seen_ids = set()
        for match in candidates:
            row_id = max(0, ss_normalization._coerce_int(match.get('id'), 0))
            if row_id <= 0 or row_id in seen_ids:
                continue
            if _sold_removal_barcode_key(match.get('barcode')) != target_key:
                continue
            if max(0, ss_normalization._coerce_int(match.get('quantity'), 0)) <= 0:
                continue
            seen_ids.add(row_id)
            matches.append(match)

        matches.sort(key=lambda m: (str(m.get('location_key') or ''), int(m.get('id') or 0)))
        available_qty = sum(max(0, ss_normalization._coerce_int(match.get('quantity'), 0)) for match in matches)
        if available_qty <= 0:
            return {'success': False, 'error': 'No matching live inventory was found for this older entry', 'status_code': 404}
        if available_qty < qty_n:
            return {
                'success': False,
                'error': f'Only {available_qty} unit(s) are available to delete for this older entry',
                'status_code': 409
            }

        rem_conn = sqlite3.connect('rackhistory.db')
        rem_cur = rem_conn.cursor()
        ss_inventory_history._ensure_removed_items_table(rem_cur)
        now_iso = datetime.datetime.now().isoformat()
        remaining = qty_n
        removed_rows = []

        for match in matches:
            if remaining <= 0:
                break
            row_id = int(match.get('id') or 0)
            current_qty = max(0, ss_normalization._coerce_int(match.get('quantity'), 0))
            if row_id <= 0 or current_qty <= 0:
                continue
            take_qty = min(current_qty, remaining)
            new_qty = current_qty - take_qty
            rack_cur.execute(
                f'UPDATE SEARCHRACK SET {qty_expr} = ? WHERE {id_lookup_expr} = ?',
                (new_qty, row_id)
            )
            if rack_cur.rowcount == 0:
                raise RuntimeError(f'Inventory row {row_id} changed before delete could complete')

            location = _sold_location_label(
                match.get('location_code') or
                match.get('item_position') or
                match.get('pictureposition') or
                ''
            )
            rem_cur.execute('''
                INSERT INTO removed_items
                (order_id, barcode, title, quantity_removed, removed_at, searchrack_id,
                 old_quantity, new_quantity, removal_type, item_position, event_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            ''', (
                None,
                str(match.get('barcode') or upc).strip(),
                str(match.get('title') or '').strip(),
                take_qty,
                now_iso,
                row_id,
                current_qty,
                new_qty,
                'prep_history_delete',
                location
            ))
            removed_rows.append({
                'searchrack_id': row_id,
                'from_qty': current_qty,
                'to_qty': new_qty,
                'quantity_removed': take_qty,
                'location_code': location
            })
            if new_qty <= 0:
                zero_qty_row_ids.append(row_id)
            remaining -= take_qty

        if remaining > 0:
            raise RuntimeError('Inventory changed before all requested quantity could be removed')

        rem_conn.commit()
        rack_conn.commit()
        for row_id in zero_qty_row_ids:
            try:
                ss_inventory_history._clear_zero_qty_pending_deletions(row_id)
            except Exception:
                pass
        try:
            ss_caching._invalidate_searchrack_cache()
            ss_caching.update_data_version()
        except Exception:
            pass
        return {
            'success': True,
            'upc': str(upc or '').strip(),
            'requested_qty': qty_n,
            'removed_qty': qty_n,
            'removed_rows': removed_rows
        }
    except Exception as e:
        try:
            if rack_conn is not None:
                rack_conn.rollback()
        except Exception:
            pass
        try:
            if rem_conn is not None:
                rem_conn.rollback()
        except Exception:
            pass
        return {
            'success': False,
            'error': ss_errors._safe_error(e, 'items_prep:legacy_inventory_delete'),
            'status_code': 500
        }
    finally:
        try:
            if rack_conn is not None:
                rack_conn.close()
        except Exception:
            pass
        try:
            if rem_conn is not None:
                rem_conn.close()
        except Exception:
            pass


def _searchrack_total_qty_for_key(conn, upc_key):
    """Return total warehouse qty for one normalized UPC key."""
    if not upc_key:
        return 0

    cur = conn.cursor()
    cur.execute("PRAGMA table_info('SEARCHRACK')")
    cols = [r[1] for r in cur.fetchall()]
    cols_lower = {c.lower(): c for c in cols}
    upc_col = cols_lower.get('upc') or cols_lower.get('barcode')
    qty_col = cols_lower.get('quantity') or cols_lower.get('qty')
    if not upc_col or not qty_col:
        return 0

    cur.execute(f'''
        SELECT {upc_col} AS upc, {qty_col} AS qty
        FROM SEARCHRACK
        WHERE {upc_col} IS NOT NULL AND TRIM({upc_col}) != ""
    ''')

    total_qty = 0
    for raw_upc, raw_qty in cur.fetchall():
        if ss_listing_alerts._listing_alert_upc_key(raw_upc) != upc_key:
            continue
        qty = max(0, ss_normalization._coerce_int(raw_qty, 0))
        total_qty += qty
    return total_qty


def _sold_order_value(order, key, default=''):
    if order is None:
        return default
    try:
        if isinstance(order, dict):
            return order.get(key, default)
        return order[key]
    except Exception:
        pass
    return default


def _ensure_order_processing_claim_column(cur):
    """Track in-progress Ready to Ship claims so stale ones can be recovered."""
    try:
        cur.execute('PRAGMA table_info(orders)')
        cols = {str(r[1]).lower() for r in cur.fetchall()}
        if 'rackupdated_claimed_at' not in cols:
            cur.execute('ALTER TABLE orders ADD COLUMN rackupdated_claimed_at TEXT')
    except Exception:
        pass


def _is_order_processing_claim_stale(claimed_at_value, timeout_minutes=10):
    """Treat missing or old in-progress claims as recoverable."""
    claimed_at = str(claimed_at_value or '').strip()
    if not claimed_at:
        return True
    try:
        claim_dt = datetime.datetime.fromisoformat(claimed_at.replace('Z', '+00:00'))
        if claim_dt.tzinfo is not None:
            claim_dt = claim_dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        age_seconds = (datetime.datetime.utcnow() - claim_dt).total_seconds()
        return age_seconds >= max(60, int(timeout_minutes) * 60)
    except Exception:
        return True


def _effective_sold_order_barcode(order, prefer_manual_override=False):
    source_upc = str(_sold_order_value(order, 'source_upc', '') or '').strip()
    barcode = str(_sold_order_value(order, 'barcode', '') or '').strip()

    # Finder can manually link a sold order to a different warehouse barcode.
    # In ready-to-ship/removal flows, honor that explicit override instead of
    # forcing the original traced source_upc.
    if prefer_manual_override and source_upc and barcode:
        source_key = _sold_removal_barcode_key(source_upc)
        barcode_key = _sold_removal_barcode_key(barcode)
        if source_key and barcode_key and source_key != barcode_key:
            return barcode

    # When finder explicitly matched an order (it writes both barcode AND location to sold.db)
    # and there is no source_upc to fall back to, trust the stored barcode directly instead of
    # re-resolving from the marketplace SKU (which can return a different UPC).
    if prefer_manual_override and barcode and not source_upc:
        stored_location = str(_sold_order_value(order, 'location', '') or '').strip()
        if stored_location:
            return barcode

    if source_upc:
        return source_upc
    sku = str(_sold_order_value(order, 'sku', '') or '').strip()
    platform = str(_sold_order_value(order, 'store', '') or '').strip().lower()
    item_id = str(_sold_order_value(order, 'item_id', '') or '').strip()
    if not sku or sku.lower() in {'none', 'null', 'n/a', 'na', 'does not apply'}:
        return barcode
    try:
        resolved = resolve_barcode_from_marketplace_sku(
            sku,
            platform=platform,
            item_id=item_id,
            fallback_barcode=barcode
        )
        return str(resolved or barcode).strip()
    except Exception:
        return barcode
