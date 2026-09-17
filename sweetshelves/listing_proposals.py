"""Listing proposals: the auto-listing agent's eligibility gate, proposal builder and review actions.

Phase 1 of the listing agent. The agent walks a queued UPC through the existing Listing Agent
endpoints (catalog search, category suggestions, aspects, comps, AI title and description, dry-run
draft) and stores the result as a proposal. Nothing reaches eBay until a person approves the
proposal on the review page, and every step is written to listing_proposal_events.
"""

import datetime
import json
import os
import re
import statistics
import threading
import requests
from flask import has_request_context, jsonify, render_template, request
from . import (
    database as ss_database, ebay_catalog as ss_ebay_catalog, ebay_publish as ss_ebay_publish, errors as
    ss_errors, listing_checks as ss_listing_checks, listing_queue as ss_listing_queue, listing_settings as
    ss_listing_settings, normalization as ss_normalization, runtime as ss_runtime, warehouse_matching as
    ss_warehouse_matching,
)


AGENT_VERSION = 'phase1'

# Fixed shot list (decided 2026-09-13): the same four shots for every item.
SHOT_LIST = ('front', 'back', 'label or UPC', 'any flaw')

OPEN_STATUSES = ('proposed', 'held', 'needs_photos', 'blocked')

CLOSED_STATUSES = ('listed', 'rejected', 'superseded')

PROPOSAL_FIELDS = (
    'sku', 'title', 'listingDescription', 'price', 'quantity', 'condition', 'conditionDescription',
    'categoryId', 'categoryPath', 'aspects', 'images', 'marketplaceId', 'currency', 'listingDuration',
    'merchantLocationKey', 'fulfillmentPolicyId', 'paymentPolicyId', 'returnPolicyId', 'catalogEpid',
    'lot_number',
)

CONDITION_CHOICES = (
    'NEW', 'NEW_OTHER', 'NEW_WITH_DEFECTS', 'USED_EXCELLENT', 'USED_VERY_GOOD', 'USED_GOOD',
    'USED_ACCEPTABLE', 'FOR_PARTS_OR_NOT_WORKING',
)

DAMAGE_WORDS = (
    'damage', 'damaged', 'scratch', 'scratched', 'dent', 'dented', 'broken', 'crack', 'cracked', 'torn',
    'tear', 'missing', 'stain', 'stained', 'chip', 'chipped', 'worn', 'defect', 'open box', 'opened',
)

CLAUDE_MODEL = 'claude-haiku-4-5-20251001'

_BUILD_JOB = {'running': False, 'started_at': '', 'finished_at': '', 'total': 0, 'done': 0,
              'current': '', 'results': [], 'error': ''}

_BUILD_JOB_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _proposals_init_tables(cur):
    cur.execute('''
        CREATE TABLE IF NOT EXISTS listing_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upc TEXT NOT NULL,
            queue_id INTEGER,
            status TEXT NOT NULL DEFAULT 'proposed',
            agent_version TEXT,
            eligibility_json TEXT,
            proposal_json TEXT,
            comps_json TEXT,
            sources_json TEXT,
            flags_json TEXT,
            reviewer_note TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            error TEXT,
            listed_listing_id TEXT,
            listed_offer_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_proposals_status_updated ON listing_proposals(status, updated_at)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_proposals_upc ON listing_proposals(upc)')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS listing_proposal_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id INTEGER NOT NULL,
            upc TEXT NOT NULL,
            event TEXT NOT NULL,
            actor TEXT,
            note TEXT,
            payload_json TEXT,
            created_at TEXT NOT NULL
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_proposal_events_proposal ON listing_proposal_events(proposal_id, id)')


def _proposal_now():
    return datetime.datetime.now().isoformat(timespec='seconds')


def _proposal_json_loads(text, default):
    if text in (None, ''):
        return default
    try:
        value = json.loads(text)
    except Exception:
        return default
    return value if value is not None else default


def _proposal_row_to_dict(row):
    item = ss_listing_checks._listagent_row_to_dict(row)
    if not item:
        return None
    item['eligibility'] = _proposal_json_loads(item.pop('eligibility_json', None), {})
    item['proposal'] = _proposal_json_loads(item.pop('proposal_json', None), {})
    item['comps'] = _proposal_json_loads(item.pop('comps_json', None), {})
    item['sources'] = _proposal_json_loads(item.pop('sources_json', None), {})
    item['flags'] = _proposal_json_loads(item.pop('flags_json', None), [])
    return item


def _proposal_add_event(cur, *, proposal_id, upc, event, actor='agent', note='', payload=None):
    payload_json = None
    if payload is not None:
        try:
            payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            payload_json = None
    cur.execute('''
        INSERT INTO listing_proposal_events (proposal_id, upc, event, actor, note, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (int(proposal_id), upc, event, (actor or 'agent'), (note or ''), payload_json, _proposal_now()))


def _proposal_get(proposal_id, *, with_events=True):
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _proposals_init_tables(cur)
        cur.execute('SELECT * FROM listing_proposals WHERE id = ? LIMIT 1', (int(proposal_id),))
        item = _proposal_row_to_dict(cur.fetchone())
        if not item:
            return None
        if with_events:
            cur.execute('SELECT * FROM listing_proposal_events WHERE proposal_id = ? ORDER BY id ASC', (int(proposal_id),))
            events = []
            for row in cur.fetchall():
                event = ss_listing_checks._listagent_row_to_dict(row) or {}
                event['payload'] = _proposal_json_loads(event.pop('payload_json', None), None)
                events.append(event)
            item['events'] = events
        return item


def _proposal_list(*, status='open', limit=200):
    limit = max(1, min(int(limit or 200), 1000))
    status = (status or 'open').strip().lower()
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _proposals_init_tables(cur)
        if status == 'all':
            cur.execute('SELECT * FROM listing_proposals ORDER BY updated_at DESC, id DESC LIMIT ?', (limit,))
        elif status == 'open':
            placeholders = ','.join('?' for _ in OPEN_STATUSES)
            cur.execute(f'SELECT * FROM listing_proposals WHERE status IN ({placeholders}) ORDER BY updated_at DESC, id DESC LIMIT ?',
                        (*OPEN_STATUSES, limit))
        else:
            cur.execute('SELECT * FROM listing_proposals WHERE status = ? ORDER BY updated_at DESC, id DESC LIMIT ?', (status, limit))
        rows = [_proposal_row_to_dict(r) for r in cur.fetchall()]
        cur.execute('SELECT status, COUNT(*) AS n FROM listing_proposals GROUP BY status')
        counts = {str(r['status'] or ''): int(r['n'] or 0) for r in cur.fetchall()}
    return [r for r in rows if r], counts


def _proposal_summary(item):
    proposal = item.get('proposal') or {}
    images = [im for im in (proposal.get('images') or []) if isinstance(im, dict) and im.get('enabled', True)]
    eligibility = item.get('eligibility') or {}
    return {
        'id': item.get('id'),
        'upc': item.get('upc'),
        'status': item.get('status'),
        'title': proposal.get('title') or '',
        'price': proposal.get('price'),
        'quantity': proposal.get('quantity'),
        'thumb': (images[0].get('url') if images else ''),
        'flags': item.get('flags') or [],
        'flag_count': len([f for f in (item.get('flags') or []) if (f.get('level') or '') in ('warn', 'block')]),
        'listable_quantity': eligibility.get('listable_quantity'),
        'reviewer_note': item.get('reviewer_note') or '',
        'reviewed_by': item.get('reviewed_by') or '',
        'updated_at': item.get('updated_at') or '',
        'created_at': item.get('created_at') or '',
        'error': item.get('error') or '',
        'listed_listing_id': item.get('listed_listing_id') or '',
    }


def _proposal_supersede_open(cur, upc, *, actor='agent', note='rebuilt'):
    variants = ss_listing_queue._listagent_upc_variants(upc)
    placeholders = ','.join('?' for _ in variants)
    status_placeholders = ','.join('?' for _ in OPEN_STATUSES)
    cur.execute(f'SELECT id, upc FROM listing_proposals WHERE upc IN ({placeholders}) AND status IN ({status_placeholders})',
                (*variants, *OPEN_STATUSES))
    now = _proposal_now()
    for row in cur.fetchall():
        cur.execute("UPDATE listing_proposals SET status = 'superseded', updated_at = ? WHERE id = ?", (now, row['id']))
        _proposal_add_event(cur, proposal_id=row['id'], upc=row['upc'], event='superseded', actor=actor, note=note)


# ---------------------------------------------------------------------------
# Eligibility gate
# ---------------------------------------------------------------------------

def _proposal_upc_variants(upc):
    variants = list(ss_listing_queue._listagent_upc_variants(upc))
    normalized = ss_normalization._normalize_upc_preserve_suffix_for_match(upc)
    if normalized and normalized not in variants:
        variants.append(normalized)
    return variants


def _agent_prep_rows(upc):
    """Prep records for a UPC across lots, from items_prep_status in bol.db."""
    variants = _proposal_upc_variants(upc)
    if not variants:
        return []
    placeholders = ','.join('?' for _ in variants)
    try:
        with ss_database.db_connection('bol.db') as conn:
            cur = conn.cursor()
            cur.execute(f'''
                SELECT upc, COALESCE(lot_number, '') AS lot_number, COALESCE(status, '') AS status,
                       COALESCE(reason, '') AS reason, COALESCE(note, '') AS note, COALESCE(updated_at, '') AS updated_at,
                       COALESCE(location, '') AS location, COALESCE(pictureposition, '') AS pictureposition,
                       COALESCE(quantity, 1) AS quantity
                FROM items_prep_status
                WHERE upc COLLATE NOCASE IN ({placeholders})
                ORDER BY updated_at DESC
            ''', tuple(variants))
            rows = [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    for row in rows:
        row['status'] = str(row.get('status') or '').strip().lower()
        row['quantity'] = max(0, ss_normalization._coerce_int(row.get('quantity'), 0))
    return rows


def _agent_rack_rows(upc):
    """Rack rows with quantity for the barcode, using the shared SEARCHRACK matcher."""
    try:
        with ss_database.db_connection('searchRack.db') as conn:
            cur = conn.cursor()
            rows = ss_warehouse_matching._searchrack_matches_for_barcode(cur, upc)
            if not rows:
                # The shelf row can carry the unit suffix while the queue holds the base UPC
                # ("076440150179" queued, "076440150179-1" scanned onto the shelf). The matcher
                # only walks the other way (suffixed target -> base row), so ask for the units.
                rows = ss_warehouse_matching._ready_to_ship_suffix_inventory_matches(cur, upc)
            return rows
    except Exception:
        return []


def _agent_live_listings(upc):
    """Live eBay and Amazon listings for the UPC from the local store databases."""
    variants = [v.lower() for v in _proposal_upc_variants(upc)]
    out = {'ebay': [], 'amazon': []}
    if not variants:
        return out
    placeholders = ','.join('?' for _ in variants)
    try:
        with ss_database.db_connection('ebayStore.db') as conn:
            cur = conn.cursor()
            cur.execute(f'''
                SELECT Title, ItemID, SKU, Price, Quantity, URL, List_State
                FROM INVENTORY
                WHERE LOWER(TRIM(COALESCE(UPC, ''))) IN ({placeholders})
                  AND LOWER(TRIM(COALESCE(List_State, ''))) IN ('active', 'live', 'listed')
                  AND TRIM(COALESCE(ItemID, '')) != ''
                  AND COALESCE(CAST(Quantity AS INTEGER), 0) > 0
            ''', tuple(variants))
            for r in cur.fetchall():
                out['ebay'].append({
                    'title': r['Title'] or '', 'item_id': str(r['ItemID'] or ''), 'sku': r['SKU'] or '',
                    'price': r['Price'], 'quantity': ss_normalization._coerce_int(r['Quantity'], 0),
                    'url': r['URL'] or (f"https://www.ebay.com/itm/{r['ItemID']}" if r['ItemID'] else ''),
                })
    except Exception:
        pass
    try:
        with ss_database.db_connection('amazonStore.db') as conn:
            cur = conn.cursor()
            cur.execute(f'''
                SELECT ASIN, SKU, TITLE, PRICE, QUANTITY, STATUS
                FROM ITEMS
                WHERE LOWER(TRIM(COALESCE(UPC, ''))) IN ({placeholders})
                  AND LOWER(TRIM(COALESCE(STATUS, ''))) IN ('active', 'live', 'listed')
                  AND TRIM(COALESCE(ASIN, '')) != ''
                  AND COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0
            ''', tuple(variants))
            for r in cur.fetchall():
                out['amazon'].append({
                    'title': r['TITLE'] or '', 'asin': r['ASIN'] or '', 'sku': r['SKU'] or '',
                    'price': r['PRICE'], 'quantity': ss_normalization._coerce_int(r['QUANTITY'], 0),
                })
    except Exception:
        pass
    return out


def _agent_cost(upc):
    """Latest manifest cost for the UPC from rawbol.db (avg_cost and original retail)."""
    base = (upc or '').split('-', 1)[0].strip()
    variants = []
    for v in (base, base.lstrip('0') or base, base.zfill(12) if base.isdigit() else base):
        if v and v not in variants:
            variants.append(v)
    if not variants:
        return {}
    placeholders = ','.join('?' for _ in variants)
    try:
        with ss_database.db_connection('rawbol.db') as conn:
            cur = conn.cursor()
            cur.execute(f'''
                SELECT avg_cost, original_retail, lot_number, item_description
                FROM raw_bol_items
                WHERE TRIM(upc) IN ({placeholders})
                ORDER BY import_date DESC, id DESC
                LIMIT 1
            ''', tuple(variants))
            row = cur.fetchone()
    except Exception:
        return {}
    if not row:
        return {}
    out = {'lot_number': row['lot_number'] or '', 'description': row['item_description'] or ''}
    for key in ('avg_cost', 'original_retail'):
        try:
            value = float(row[key]) if row[key] is not None else None
        except Exception:
            value = None
        out[key] = value if value is not None and value > 0 else None
    return out


def _agent_eligibility(upc):
    """Run the gate. Returns checks plus the numbers the review card shows."""
    prep_rows = _agent_prep_rows(upc)
    rack_rows = _agent_rack_rows(upc)
    live = _agent_live_listings(upc)

    good_rows = [r for r in prep_rows if r.get('status') == 'good']
    prep_qty = sum(int(r.get('quantity') or 0) for r in good_rows)
    lots = []
    for r in good_rows:
        lot = str(r.get('lot_number') or '').strip()
        if lot and lot not in lots:
            lots.append(lot)
    rack_qty = sum(int(r.get('quantity') or 0) for r in rack_rows)
    rack_locations = []
    for r in rack_rows:
        loc = str(r.get('location_code') or r.get('item_position') or '').strip()
        if loc and loc not in rack_locations:
            rack_locations.append(loc)
    prep_locations = [str(r.get('location') or '').strip() for r in good_rows if str(r.get('location') or '').strip()]
    ebay_live_qty = sum(int(r.get('quantity') or 0) for r in live['ebay'])

    checks = []

    def add(key, label, status, detail, source):
        checks.append({'key': key, 'label': label, 'status': status, 'detail': detail, 'source': source})

    if good_rows:
        add('prepped', 'Prepped', 'pass', f"{prep_qty} good in {len(lots) or 1} lot(s): {', '.join(lots) or 'no lot'}", 'items_prep_status')
    elif prep_rows:
        statuses = sorted({r.get('status') or 'unknown' for r in prep_rows})
        add('prepped', 'Prepped', 'block', f"Prep record exists but status is {', '.join(statuses)}", 'items_prep_status')
    else:
        add('prepped', 'Prepped', 'block', 'No prep record for this UPC', 'items_prep_status')

    if rack_qty > 0:
        add('traceable', 'On a shelf', 'pass', f"{rack_qty} on rack at {', '.join(rack_locations) or 'unknown location'}", 'SEARCHRACK')
    else:
        add('traceable', 'On a shelf', 'block', 'No rack row with quantity for this barcode', 'SEARCHRACK')

    location_mismatch = False
    if prep_locations and rack_locations:
        rack_keys = {ss_warehouse_matching._sold_location_key(loc) for loc in rack_locations}
        for loc in prep_locations:
            if ss_warehouse_matching._sold_location_key(loc) not in rack_keys:
                location_mismatch = True
        if location_mismatch:
            add('location', 'Location matches prep', 'flag',
                f"Prep says {', '.join(sorted(set(prep_locations)))}, rack says {', '.join(rack_locations)}", 'items_prep_status vs SEARCHRACK')
        else:
            add('location', 'Location matches prep', 'pass', ', '.join(rack_locations), 'items_prep_status vs SEARCHRACK')

    listable = min(prep_qty, rack_qty) if (prep_qty and rack_qty) else 0
    quantity_mismatch = bool(prep_qty and rack_qty and prep_qty != rack_qty)
    if quantity_mismatch:
        add('quantity', 'Quantity', 'flag', f"Prep says {prep_qty}, rack says {rack_qty}. Listing the lower number ({listable}) and asking for a recount.", 'items_prep_status vs SEARCHRACK')
    elif listable:
        add('quantity', 'Quantity', 'pass', f"{listable} available", 'items_prep_status vs SEARCHRACK')
    else:
        add('quantity', 'Quantity', 'block', 'Nothing listable until prep and rack both show stock', 'items_prep_status vs SEARCHRACK')

    if live['ebay']:
        ids = ', '.join(r.get('item_id') or '' for r in live['ebay'])
        add('duplicate', 'Not already on eBay', 'flag', f"Live eBay listing(s) {ids} with {ebay_live_qty} unit(s). Revise that listing instead of creating another.", 'ebayStore.db')
    else:
        add('duplicate', 'Not already on eBay', 'pass', 'No live eBay listing for this UPC', 'ebayStore.db')
    if live['amazon']:
        add('amazon', 'Amazon', 'info', f"Also live on Amazon ({len(live['amazon'])} listing(s)). Units are shared across channels.", 'amazonStore.db')

    blocked = any(c['status'] == 'block' for c in checks)
    return {
        'ok': not blocked,
        'blocked': blocked,
        'checks': checks,
        'prep': {'rows': prep_rows, 'good_quantity': prep_qty, 'lots': lots, 'locations': sorted(set(prep_locations))},
        'rack': {'rows': rack_rows, 'quantity': rack_qty, 'locations': rack_locations},
        'live': live,
        'listable_quantity': listable,
        'quantity_mismatch': quantity_mismatch,
        'location_mismatch': location_mismatch,
        'already_on_ebay': bool(live['ebay']),
        'checked_at': _proposal_now(),
    }


# ---------------------------------------------------------------------------
# Calling the existing Listing Agent endpoints in-process
# ---------------------------------------------------------------------------

def _agent_request_base():
    if has_request_context():
        try:
            return (request.url_root or '').strip() or 'http://localhost/'
        except Exception:
            return 'http://localhost/'
    return 'http://localhost/'


def _agent_call_view(view, path, *, method='GET', query=None, json_body=None, base_url=None):
    """Run an existing view function with a synthetic request and return (json, status)."""
    kwargs = {'method': method}
    if query:
        kwargs['query_string'] = {k: str(v) for k, v in query.items() if v not in (None, '')}
    if json_body is not None:
        kwargs['json'] = json_body
    with ss_runtime.app.test_request_context(path, base_url=(base_url or _agent_request_base()), **kwargs):
        rv = view()
    status = 200
    if isinstance(rv, tuple):
        rv, status = rv[0], int(rv[1] or 200)
    data = rv.get_json(silent=True) if hasattr(rv, 'get_json') else rv
    return (data or {}), status


def _agent_relativize(url, base_url):
    """Store local static URLs as paths so they can be re-based at publish time."""
    value = str(url or '').strip()
    base = (base_url or '').rstrip('/')
    if base and value.startswith(base + '/'):
        return value[len(base):]
    return value


def _agent_upc_detail(upc, *, base_url=None):
    data, _status = _agent_call_view(
        lambda: ss_listing_queue.api_listingagent_upc_detail(upc),
        f'/api/listingagent/upc/{upc}', base_url=base_url)
    return data.get('item') or {} if data.get('success') else {}


def _agent_catalog_candidates(upc, title, marketplace_id, *, base_url=None):
    data, _status = _agent_call_view(
        ss_ebay_catalog.api_listingagent_ebay_catalog_search, '/api/listingagent/ebay/catalog_search',
        query={'upc': upc, 'title': title, 'limit': 8, 'marketplaceId': marketplace_id}, base_url=base_url)
    return list(data.get('results') or []) if data.get('success') else []


def _agent_category_suggestions(q, marketplace_id, *, base_url=None):
    data, _status = _agent_call_view(
        ss_ebay_catalog.api_listingagent_ebay_category_suggestions, '/api/listingagent/ebay/category_suggestions',
        query={'q': q, 'limit': 8, 'marketplaceId': marketplace_id}, base_url=base_url)
    return list(data.get('results') or []) if data.get('success') else []


def _agent_category_aspects(category_id, marketplace_id):
    try:
        return list(ss_ebay_catalog._listingagent_get_ebay_category_aspects(category_id, marketplace_id, values_limit=40) or [])
    except Exception:
        return []


def _agent_comps(upc, marketplace_id, *, base_url=None):
    data, _status = _agent_call_view(
        ss_ebay_catalog.api_listingagent_ebay_comps, '/api/listingagent/ebay/comps',
        query={'upc': upc, 'limit': 24, 'marketplaceId': marketplace_id}, base_url=base_url)
    if not data.get('success'):
        return [], (data.get('error') or 'comps unavailable')
    return list(data.get('results') or []), ''


def _agent_generate_title(payload, *, base_url=None):
    data, _status = _agent_call_view(
        ss_ebay_publish.api_listingagent_ebay_generate_title, '/api/listingagent/ebay/generate_title',
        method='POST', json_body=payload, base_url=base_url)
    return (data.get('title') or '').strip() if data.get('success') else ''


def _agent_generate_description(payload, *, base_url=None):
    data, _status = _agent_call_view(
        ss_ebay_publish.api_listingagent_ebay_generate_description, '/api/listingagent/ebay/generate_description',
        method='POST', json_body=payload, base_url=base_url)
    return (data.get('description') or '').strip() if data.get('success') else ''


def _agent_dry_run(payload, *, base_url=None):
    """Build the eBay payloads without sending them. Returns (result, error)."""
    try:
        with ss_runtime.app.test_request_context('/api/listingagent/ebay/draft', base_url=(base_url or _agent_request_base())):
            return ss_ebay_publish._listingagent_ebay_create_draft_offer(dict(payload), dry_run=True), ''
    except ss_errors._ListingAgentUserError as e:
        return None, str(e)
    except Exception as e:
        return None, f'{type(e).__name__}: {e}'


def _agent_publish(payload, *, base_url=None):
    """Publish through the existing endpoint. Returns (json, status)."""
    return _agent_call_view(
        ss_ebay_publish.api_listingagent_ebay_publish, '/api/listingagent/ebay/publish',
        method='POST', json_body=payload, base_url=base_url)


def _agent_claude_json(prompt, *, max_tokens=700):
    """Ask Claude for a JSON object. Returns a dict, or None when unavailable or unparsable."""
    api_key = os.getenv('ANTHROPIC_API_KEY', '').strip()
    if not api_key:
        return None
    try:
        resp = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
            json={'model': CLAUDE_MODEL, 'max_tokens': int(max_tokens),
                  'messages': [{'role': 'user', 'content': prompt}]},
            timeout=40,
        )
        if resp.status_code >= 400:
            return None
        text = ((resp.json().get('content') or [{}])[0].get('text') or '').strip()
    except Exception:
        return None
    text = re.sub(r'^```[^\n]*\n?', '', text).rstrip('`').strip()
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except Exception:
        return None
    return value if isinstance(value, dict) else None


# ---------------------------------------------------------------------------
# Judgment steps (pure where possible)
# ---------------------------------------------------------------------------

def _agent_price_from_comps(comps, *, cost=None, floor_multiplier=1.25):
    """Median of current fixed-price comps, never below the cost floor."""
    prices = []
    for comp in comps or []:
        price = (comp or {}).get('price') or {}
        try:
            value = float(price.get('value'))
        except Exception:
            continue
        buying = [str(b).upper() for b in ((comp or {}).get('buyingOptions') or [])]
        if buying and 'FIXED_PRICE' not in buying:
            continue
        if value > 0:
            prices.append(value)
    prices.sort()
    median = round(statistics.median(prices), 2) if prices else None
    floor = None
    try:
        if cost is not None and float(cost) > 0:
            floor = round(float(cost) * float(floor_multiplier or 1.0), 2)
    except Exception:
        floor = None
    proposed = median
    rule = 'median of current comps'
    if floor is not None and (proposed is None or proposed < floor):
        proposed = floor
        rule = 'cost floor (comps were missing or below it)' if median is not None else 'cost floor (no comps)'
    return {
        'proposed': proposed,
        'median': median,
        'low': prices[0] if prices else None,
        'high': prices[-1] if prices else None,
        'count': len(prices),
        'cost': (round(float(cost), 2) if cost else None),
        'floor': floor,
        'floor_multiplier': floor_multiplier,
        'rule': rule,
    }


def _agent_norm_aspect_name(name):
    return re.sub(r'[^a-z0-9]+', '', str(name or '').lower())


def _agent_fill_aspects(aspects_meta, candidate_aspects, *, title='', upc='', brand=''):
    """Fill item specifics from the catalog candidate, then ask Claude only for missing required ones."""
    filled = {}
    lookup = {}
    for name, value in (candidate_aspects or {}).items():
        if isinstance(value, list):
            value = ', '.join(str(v) for v in value if str(v).strip())
        value = str(value or '').strip()
        if name and value:
            lookup[_agent_norm_aspect_name(name)] = value
    if brand and 'brand' not in lookup:
        lookup['brand'] = brand
    for meta in aspects_meta or []:
        name = str(meta.get('name') or '').strip()
        if not name:
            continue
        value = lookup.get(_agent_norm_aspect_name(name))
        if value:
            filled[name] = value
    required = [str(m.get('name') or '') for m in (aspects_meta or []) if m.get('required')]
    missing = [n for n in required if n and n not in filled]
    inferred = []
    if missing and (title or upc):
        known = '\n'.join(f'- {k}: {v}' for k, v in filled.items())
        prompt = (
            'You fill eBay item specifics for a reseller. Return only a JSON object mapping each requested '
            'specific to a short value string, or null when the product information does not say. '
            'Never guess a value that is not supported by the information given.\n\n'
            f'Product title: {title}\nUPC: {upc}\n'
            + (f'Known specifics:\n{known}\n' if known else '')
            + 'Requested specifics: ' + json.dumps(missing) + '\n'
        )
        answer = _agent_claude_json(prompt) or {}
        for name in missing:
            value = answer.get(name)
            if isinstance(value, (list, tuple)):
                value = ', '.join(str(v) for v in value if str(v).strip())
            value = str(value or '').strip()
            if value and value.lower() not in ('null', 'none', 'unknown', 'n/a'):
                filled[name] = value
                inferred.append(name)
        missing = [n for n in missing if n not in filled]
    return filled, missing, inferred


def _agent_condition(detail, prep_rows, *, default_condition='NEW_OTHER', upc=''):
    """Condition from prep evidence: defect text or damage words mean used, a clean pass in Item Prep
    means new, otherwise the default."""
    notes = []
    defect = str((detail or {}).get('defect') or '').strip()
    if defect:
        notes.append(defect)
    for note in ((detail or {}).get('prep') or {}).get('notes') or []:
        text = note.get('note') if isinstance(note, dict) else note
        text = str(text or '').strip()
        if text:
            notes.append(text)
    for row in prep_rows or []:
        for key in ('note', 'reason'):
            text = str(row.get(key) or '').strip()
            if text and text not in notes:
                notes.append(text)
    joined = ' '.join(notes)
    lowered = joined.lower()
    damaged = any(word in lowered for word in DAMAGE_WORDS)
    if damaged:
        return {'condition': 'USED_GOOD', 'conditionDescription': joined[:1000], 'reason': 'prep notes mention a flaw', 'assumed': False}
    # Same rule the Lister panel uses: prep passed the unit good and wrote nothing about it, so it is new.
    prepped_good = bool(prep_rows) and all(str(r.get('status') or '').lower() == 'good' for r in prep_rows)
    if not joined and prepped_good and '-' not in str(upc or ''):
        return {'condition': 'NEW', 'conditionDescription': '', 'reason': 'Item Prep passed it good with no notes', 'assumed': False}
    return {'condition': default_condition or 'NEW_OTHER', 'conditionDescription': '', 'reason': 'no flaw recorded; default condition assumed', 'assumed': True}


def _agent_photo_sources(upc, detail, *, base_url=None):
    """Every photo we have for the UPC, own photos first, each tagged with where it came from."""
    base = (base_url or _agent_request_base()).rstrip('/')
    photos = []
    seen = set()

    def add(url, source, kind):
        value = _agent_relativize(url, base)
        if not value or value in seen:
            return
        seen.add(value)
        photos.append({'url': value, 'source': source, 'kind': kind, 'enabled': True})

    try:
        for row in ss_listing_queue._listagent_get_photos(upc, limit=60):
            rel = str(row.get('image_path') or '').strip()
            if rel:
                add(f'/static/{rel}', 'listing', 'own')
    except Exception:
        pass
    try:
        for row in ss_listing_checks._listagent_get_check_media(upc):
            if str(row.get('media_type') or 'image').lower() != 'image':
                continue
            rel = str(row.get('file_path') or '').strip()
            if rel:
                add(f'/static/{rel}', 'checker', 'own')
    except Exception:
        pass
    for url in ((detail or {}).get('prep') or {}).get('images') or []:
        add(url, 'prep', 'own')
    prep_urls = {_agent_relativize(u, base) for u in (((detail or {}).get('prep') or {}).get('images') or [])}
    for url in (detail or {}).get('images') or []:
        rel = _agent_relativize(url, base)
        if rel in prep_urls:
            continue
        add(url, 'catalog', 'catalog')
    return photos


# ---------------------------------------------------------------------------
# Proposal builder
# ---------------------------------------------------------------------------

def _agent_flag(flags, level, code, message):
    flags.append({'level': level, 'code': code, 'message': message})


def _agent_settings_snapshot():
    settings = ss_listing_settings._listingagent_get_settings() or {}
    return {
        'marketplaceId': (settings.get('ebay_marketplace_id') or 'EBAY_US').strip(),
        'currency': (settings.get('ebay_currency') or 'USD').strip(),
        'listingDuration': (settings.get('ebay_listing_duration') or 'GTC').strip(),
        'merchantLocationKey': (settings.get('ebay_location_key') or '').strip(),
        'fulfillmentPolicyId': (settings.get('ebay_fulfillment_policy_id') or '').strip(),
        'paymentPolicyId': (settings.get('ebay_payment_policy_id') or '').strip(),
        'returnPolicyId': (settings.get('ebay_return_policy_id') or '').strip(),
        'default_condition': (settings.get('listing_agent_default_condition') or 'NEW_OTHER').strip(),
        'floor_multiplier': ss_listing_settings._listingagent_parse_float(settings.get('listing_agent_floor_multiplier'), 1.25) or 1.25,
    }


def _agent_ensure_recount_request(upc, eligibility):
    """Decision 1: quantity mismatch lists the lower number and asks the floor to recount."""
    if not eligibility.get('quantity_mismatch'):
        return False
    try:
        existing = ss_listing_checks._listagent_get_check_request(upc)
        if existing and (existing.get('status') or 'open') == 'open':
            return False
        prep_qty = (eligibility.get('prep') or {}).get('good_quantity')
        rack_qty = (eligibility.get('rack') or {}).get('quantity')
        ss_listing_checks._listagent_set_check_request(
            upc=upc, check_quantity=1,
            custom_note=f'Listing agent: prep says {prep_qty}, rack says {rack_qty}. Please recount.',
            source='listing_agent')
        return True
    except Exception:
        return False


def _agent_build_proposal(upc, *, actor='agent', base_url=None):
    """Build (or rebuild) the proposal for one UPC and store it. Returns the stored proposal."""
    upc12 = ss_listing_queue._listagent_format_upc12(upc)
    if not upc12:
        raise ValueError('upc is required')
    base_url = base_url or _agent_request_base()
    queue_item = (ss_listing_queue._listagent_get_queue_statuses([upc12]) or {}).get(upc12) or {}
    settings = _agent_settings_snapshot()
    marketplace_id = settings['marketplaceId']

    eligibility = _agent_eligibility(upc12)
    flags = []
    for check in eligibility['checks']:
        if check['status'] == 'block':
            _agent_flag(flags, 'block', check['key'], f"{check['label']}: {check['detail']}")
        elif check['status'] == 'flag':
            _agent_flag(flags, 'warn', check['key'], f"{check['label']}: {check['detail']}")
        elif check['status'] == 'info':
            _agent_flag(flags, 'info', check['key'], f"{check['label']}: {check['detail']}")

    proposal = {}
    comps_block = {}
    sources = {'settings': settings, 'queue': queue_item}
    status = 'proposed'

    if eligibility['blocked']:
        status = 'blocked'
    else:
        detail = _agent_upc_detail(upc12, base_url=base_url)
        system_title = str(detail.get('title') or queue_item.get('title') or '').strip()
        photos = _agent_photo_sources(upc12, detail, base_url=base_url)
        own_count = len([p for p in photos if p.get('kind') == 'own'])
        if not photos:
            _agent_flag(flags, 'block', 'no_photos', 'No photo of any kind for this item. eBay needs at least one.')
            status = 'needs_photos'
        elif own_count == 0:
            _agent_flag(flags, 'warn', 'catalog_photos_only', 'Only catalog images. Real photos of the unit are missing.')
        elif own_count < len(SHOT_LIST):
            _agent_flag(flags, 'info', 'photos_short', f'{own_count} of {len(SHOT_LIST)} shots on file ({", ".join(SHOT_LIST)}).')

        candidates = _agent_catalog_candidates(upc12, system_title, marketplace_id, base_url=base_url)
        best = candidates[0] if candidates else {}
        sources['catalog'] = {'best': best, 'count': len(candidates)}
        if not best:
            _agent_flag(flags, 'warn', 'no_catalog_match', 'No eBay catalog or live match. Title and specifics come from the inventory record only.')

        category_id = str(best.get('categoryId') or '').strip()
        category_path = str(best.get('categoryPath') or best.get('categoryName') or '').strip()
        suggestions = _agent_category_suggestions(best.get('title') or system_title, marketplace_id, base_url=base_url) if (best.get('title') or system_title) else []
        sources['category_suggestions'] = suggestions
        if not category_id and suggestions:
            category_id = str(suggestions[0].get('categoryId') or '').strip()
            category_path = str(suggestions[0].get('path') or '').strip()
        elif category_id and not category_path:
            for s in suggestions:
                if str(s.get('categoryId') or '') == category_id:
                    category_path = str(s.get('path') or '')
        if not category_id:
            _agent_flag(flags, 'block', 'no_category', 'No eBay category found. Pick one on the card before approving.')

        aspects_meta = _agent_category_aspects(category_id, marketplace_id) if category_id else []
        sources['aspects_meta'] = aspects_meta
        aspects, missing_required, inferred = _agent_fill_aspects(
            aspects_meta, best.get('aspects') or {}, title=(best.get('title') or system_title), upc=upc12,
            brand=str(best.get('brand') or ''))
        sources['aspects_inferred'] = inferred
        if missing_required:
            _agent_flag(flags, 'block', 'missing_required_aspects', 'Required specifics still empty: ' + ', '.join(missing_required))
        if inferred:
            _agent_flag(flags, 'info', 'aspects_inferred', 'Filled from the title by Claude, please confirm: ' + ', '.join(inferred))

        title_payload = {'title': best.get('title') or system_title, 'system_title': system_title,
                         'category_id': category_id, 'aspects': aspects, 'upc': upc12}
        title = _agent_generate_title(title_payload, base_url=base_url) or (best.get('title') or system_title)[:80]
        if not title:
            _agent_flag(flags, 'block', 'no_title', 'No title could be produced.')
        description = _agent_generate_description({**title_payload, 'listing_title': title}, base_url=base_url)
        if not description:
            _agent_flag(flags, 'warn', 'no_description', 'Description generation failed; a plain one was used.')
            description = f'<h2>{title}</h2><p>{system_title}</p>'

        comps, comps_error = _agent_comps(upc12.split('-', 1)[0], marketplace_id, base_url=base_url)
        cost = _agent_cost(upc12)
        pricing = _agent_price_from_comps(comps, cost=cost.get('avg_cost'), floor_multiplier=settings['floor_multiplier'])
        pricing['original_retail'] = cost.get('original_retail')
        comps_block = {'results': comps, 'error': comps_error, 'pricing': pricing, 'cost': cost}
        if pricing.get('proposed') is None:
            _agent_flag(flags, 'block', 'no_price', 'No comps and no cost on file. Set a price before approving.')
        elif not comps:
            _agent_flag(flags, 'warn', 'no_comps', 'No current comps; price is the cost floor.')

        condition = _agent_condition(detail, eligibility['prep']['rows'], default_condition=settings['default_condition'], upc=upc12)
        if condition.get('assumed'):
            _agent_flag(flags, 'info', 'condition_assumed', f"Condition {condition['condition']} assumed; confirm it.")
        elif condition['condition'] == 'NEW':
            _agent_flag(flags, 'info', 'condition_new', 'Item Prep passed it good with no notes, so the condition is New.')
        else:
            _agent_flag(flags, 'warn', 'damage_noted', 'Prep notes mention a flaw. Check the condition description.')

        missing_policies = [k for k in ('fulfillmentPolicyId', 'paymentPolicyId', 'returnPolicyId') if not settings.get(k)]
        if missing_policies:
            _agent_flag(flags, 'block', 'missing_policies', 'Business policies not set in Listing Agent settings: ' + ', '.join(missing_policies))

        proposal = {
            'upc': upc12,
            'sku': upc12,
            'title': title,
            'listingDescription': description,
            'price': pricing.get('proposed'),
            'quantity': eligibility['listable_quantity'],
            'condition': condition['condition'],
            'conditionDescription': condition['conditionDescription'],
            'categoryId': category_id,
            'categoryPath': category_path,
            'aspects': aspects,
            'images': photos,
            'marketplaceId': marketplace_id,
            'currency': settings['currency'],
            'listingDuration': settings['listingDuration'],
            'merchantLocationKey': settings['merchantLocationKey'],
            'fulfillmentPolicyId': settings['fulfillmentPolicyId'],
            'paymentPolicyId': settings['paymentPolicyId'],
            'returnPolicyId': settings['returnPolicyId'],
            'catalogEpid': str(best.get('epid') or ''),
            'lot_number': (eligibility['prep']['lots'][0] if eligibility['prep']['lots'] else ''),
            'system_title': system_title,
            'condition_reason': condition.get('reason', ''),
        }

        if not any(f['level'] == 'block' for f in flags):
            dry, dry_error = _agent_dry_run(_agent_publish_payload(proposal, base_url=base_url), base_url=base_url)
            sources['dry_run'] = {'ok': bool(dry), 'error': dry_error, 'offer': (dry or {}).get('offer'), 'inventoryItem': (dry or {}).get('inventoryItem')}
            if dry_error:
                _agent_flag(flags, 'warn', 'dry_run_failed', f'Payload check failed: {dry_error}')

        if eligibility.get('already_on_ebay'):
            status = 'held'
        elif status != 'needs_photos':
            status = 'proposed'

    if _agent_ensure_recount_request(upc12, eligibility):
        _agent_flag(flags, 'info', 'recount_requested', 'A recount request was sent to the floor.')

    now = _proposal_now()
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _proposals_init_tables(cur)
        _proposal_supersede_open(cur, upc12, actor=actor)
        cur.execute('''
            INSERT INTO listing_proposals (
                upc, queue_id, status, agent_version, eligibility_json, proposal_json, comps_json,
                sources_json, flags_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            upc12, queue_item.get('id'), status, AGENT_VERSION,
            json.dumps(eligibility, ensure_ascii=False, default=str),
            json.dumps(proposal, ensure_ascii=False, default=str),
            json.dumps(comps_block, ensure_ascii=False, default=str),
            json.dumps(sources, ensure_ascii=False, default=str),
            json.dumps(flags, ensure_ascii=False),
            now, now,
        ))
        proposal_id = cur.lastrowid
        _proposal_add_event(cur, proposal_id=proposal_id, upc=upc12, event='proposed', actor=actor,
                            note=status, payload={'flags': flags, 'listable_quantity': eligibility['listable_quantity']})
    return _proposal_get(proposal_id)


def _agent_publish_payload(proposal, *, base_url=None):
    """The exact body the Listing Agent page would send to /api/listingagent/ebay/publish."""
    base = (base_url or _agent_request_base()).rstrip('/')
    images = []
    for image in proposal.get('images') or []:
        if isinstance(image, dict):
            if not image.get('enabled', True):
                continue
            url = str(image.get('url') or '').strip()
        else:
            url = str(image or '').strip()
        if not url:
            continue
        if url.startswith('/'):
            url = f'{base}{url}'
        if url not in images:
            images.append(url)
    payload = {key: proposal.get(key) for key in PROPOSAL_FIELDS if key in proposal}
    payload['upc'] = proposal.get('upc')
    payload['description'] = proposal.get('title')
    payload['images'] = images[:12]
    payload['aspects'] = dict(proposal.get('aspects') or {})
    payload.pop('categoryPath', None)
    return payload


def _agent_queued_candidates(*, limit=100):
    """Queued UPCs (added by a person) that have no open proposal yet."""
    rows = ss_listing_queue._listagent_get_queue(limit=200, added_mode='my')
    queued = [r for r in rows if (r.get('status') or '') == 'queued']
    if not queued:
        return []
    open_rows, _counts = _proposal_list(status='open', limit=1000)
    open_upcs = set()
    for r in open_rows:
        for v in ss_listing_queue._listagent_upc_variants(r.get('upc')):
            open_upcs.add(v)
    out = []
    for r in queued:
        if any(v in open_upcs for v in ss_listing_queue._listagent_upc_variants(r.get('upc'))):
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out


def _agent_build_many(upcs, *, actor='agent', base_url=None):
    """Background build over several UPCs; state is readable from build_status."""
    with _BUILD_JOB_LOCK:
        _BUILD_JOB.update({'running': True, 'started_at': _proposal_now(), 'finished_at': '', 'total': len(upcs),
                           'done': 0, 'current': '', 'results': [], 'error': ''})
    try:
        with ss_runtime.app.app_context():
            for upc in upcs:
                with _BUILD_JOB_LOCK:
                    _BUILD_JOB['current'] = upc
                try:
                    item = _agent_build_proposal(upc, actor=actor, base_url=base_url)
                    result = {'upc': upc, 'id': item.get('id') if item else None, 'status': (item or {}).get('status'), 'error': ''}
                except Exception as e:
                    result = {'upc': upc, 'id': None, 'status': 'error', 'error': f'{type(e).__name__}: {e}'}
                with _BUILD_JOB_LOCK:
                    _BUILD_JOB['done'] += 1
                    _BUILD_JOB['results'].append(result)
    except Exception as e:
        with _BUILD_JOB_LOCK:
            _BUILD_JOB['error'] = f'{type(e).__name__}: {e}'
    finally:
        with _BUILD_JOB_LOCK:
            _BUILD_JOB['running'] = False
            _BUILD_JOB['current'] = ''
            _BUILD_JOB['finished_at'] = _proposal_now()


# ---------------------------------------------------------------------------
# Review actions
# ---------------------------------------------------------------------------

def _agent_apply_edits(proposal, edits):
    """Merge reviewer edits into the proposal. Only known fields, typed."""
    out = dict(proposal or {})
    edits = edits or {}
    for key in ('sku', 'title', 'listingDescription', 'condition', 'conditionDescription', 'categoryId',
                'categoryPath', 'marketplaceId', 'currency', 'listingDuration', 'merchantLocationKey',
                'fulfillmentPolicyId', 'paymentPolicyId', 'returnPolicyId', 'catalogEpid', 'lot_number'):
        if key in edits and edits[key] is not None:
            out[key] = str(edits[key]).strip()
    if 'price' in edits:
        out['price'] = ss_listing_settings._listingagent_parse_float(edits.get('price'), out.get('price'))
    if 'quantity' in edits:
        out['quantity'] = ss_listing_settings._listingagent_parse_int(edits.get('quantity'), out.get('quantity'))
    if isinstance(edits.get('aspects'), dict):
        out['aspects'] = {str(k).strip(): str(v).strip() for k, v in edits['aspects'].items() if str(k).strip() and str(v or '').strip()}
    if isinstance(edits.get('images'), list):
        images = []
        for image in edits['images']:
            if isinstance(image, dict) and str(image.get('url') or '').strip():
                images.append({'url': str(image.get('url')).strip(), 'source': str(image.get('source') or ''),
                               'kind': str(image.get('kind') or ''), 'enabled': bool(image.get('enabled', True))})
            elif isinstance(image, str) and image.strip():
                images.append({'url': image.strip(), 'source': '', 'kind': '', 'enabled': True})
        out['images'] = images
    return out


def _agent_validate_for_publish(proposal, sources):
    """What still blocks approval. Empty list means ready."""
    problems = []
    for key, label in (('title', 'title'), ('listingDescription', 'description'), ('categoryId', 'category'),
                       ('fulfillmentPolicyId', 'shipping policy'), ('paymentPolicyId', 'payment policy'),
                       ('returnPolicyId', 'return policy'), ('condition', 'condition')):
        if not str(proposal.get(key) or '').strip():
            problems.append(f'missing {label}')
    price = ss_listing_settings._listingagent_parse_float(proposal.get('price'), None)
    if price is None or price <= 0:
        problems.append('price must be above zero')
    quantity = ss_listing_settings._listingagent_parse_int(proposal.get('quantity'), 0) or 0
    if quantity < 1:
        problems.append('quantity must be at least 1')
    images = [im for im in (proposal.get('images') or []) if (im.get('enabled', True) if isinstance(im, dict) else True)]
    if not images:
        problems.append('at least one photo must be enabled')
    aspects = proposal.get('aspects') or {}
    for meta in (sources or {}).get('aspects_meta') or []:
        if meta.get('required') and not str(aspects.get(meta.get('name')) or '').strip():
            problems.append(f"required specific '{meta.get('name')}' is empty")
    return problems


def _agent_save_review(proposal_id, *, edits=None, note=None, reviewed_by=None, actor=None, event='edited'):
    item = _proposal_get(proposal_id, with_events=False)
    if not item:
        raise ss_errors._ListingAgentUserError('proposal not found', status_code=404)
    merged = _agent_apply_edits(item.get('proposal') or {}, edits)
    now = _proposal_now()
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _proposals_init_tables(cur)
        cur.execute('''
            UPDATE listing_proposals
            SET proposal_json = ?, reviewer_note = COALESCE(?, reviewer_note), reviewed_by = COALESCE(?, reviewed_by),
                reviewed_at = ?, updated_at = ?
            WHERE id = ?
        ''', (json.dumps(merged, ensure_ascii=False, default=str), note, reviewed_by, now, now, int(proposal_id)))
        changed = {k: merged.get(k) for k in (edits or {}).keys() if k in merged}
        _proposal_add_event(cur, proposal_id=proposal_id, upc=item['upc'], event=event, actor=(actor or reviewed_by or 'reviewer'),
                            note=(note or ''), payload={'changed': changed} if changed else None)
    return _proposal_get(proposal_id)


def _agent_set_status(proposal_id, status, *, actor='reviewer', note='', event=None, error=None, listing_id=None, offer_id=None):
    now = _proposal_now()
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _proposals_init_tables(cur)
        cur.execute('SELECT upc FROM listing_proposals WHERE id = ? LIMIT 1', (int(proposal_id),))
        row = cur.fetchone()
        if not row:
            raise ss_errors._ListingAgentUserError('proposal not found', status_code=404)
        cur.execute('''
            UPDATE listing_proposals
            SET status = ?, updated_at = ?, error = ?, reviewed_by = COALESCE(?, reviewed_by), reviewed_at = ?,
                listed_listing_id = COALESCE(?, listed_listing_id), listed_offer_id = COALESCE(?, listed_offer_id)
            WHERE id = ?
        ''', (status, now, error, (actor if actor != 'agent' else None), now, listing_id, offer_id, int(proposal_id)))
        _proposal_add_event(cur, proposal_id=proposal_id, upc=row['upc'], event=(event or status), actor=actor, note=note,
                            payload={'error': error} if error else None)
    return _proposal_get(proposal_id)


def _agent_approve(proposal_id, *, actor='reviewer', note='', edits=None, base_url=None):
    """Approve and publish. Re-checks the gate, validates, dry-runs, then publishes through the existing route."""
    if edits or note:
        _agent_save_review(proposal_id, edits=edits, note=note, reviewed_by=actor, actor=actor, event='edited')
    item = _proposal_get(proposal_id, with_events=False)
    if not item:
        raise ss_errors._ListingAgentUserError('proposal not found', status_code=404)
    if item.get('status') in ('listed', 'rejected', 'superseded'):
        raise ss_errors._ListingAgentUserError(f"proposal is {item.get('status')}", status_code=409)

    eligibility = _agent_eligibility(item['upc'])
    if eligibility['blocked']:
        blocked = '; '.join(c['detail'] for c in eligibility['checks'] if c['status'] == 'block')
        _agent_set_status(proposal_id, 'blocked', actor=actor, note=blocked, event='approve_refused', error=blocked)
        raise ss_errors._ListingAgentUserError(f'Gate failed on re-check: {blocked}', status_code=409)

    proposal = dict(item.get('proposal') or {})
    if eligibility['listable_quantity'] and (proposal.get('quantity') or 0) > eligibility['listable_quantity']:
        proposal['quantity'] = eligibility['listable_quantity']
    problems = _agent_validate_for_publish(proposal, item.get('sources') or {})
    if problems:
        raise ss_errors._ListingAgentUserError('Not ready to publish: ' + '; '.join(problems), status_code=400,
                                               extra={'problems': problems})

    base_url = base_url or _agent_request_base()
    payload = _agent_publish_payload(proposal, base_url=base_url)
    dry, dry_error = _agent_dry_run(payload, base_url=base_url)
    if dry_error:
        _agent_set_status(proposal_id, item['status'], actor=actor, note=dry_error, event='publish_failed', error=dry_error)
        raise ss_errors._ListingAgentUserError(f'Dry run failed: {dry_error}', status_code=400)

    _agent_set_status(proposal_id, 'approved', actor=actor, note=note, event='approved')
    payload['dry_run'] = False
    data, status_code = _agent_publish(payload, base_url=base_url)
    if not data.get('success'):
        error = str(data.get('error') or f'publish returned HTTP {status_code}')
        _agent_set_status(proposal_id, 'proposed', actor=actor, note=error, event='publish_failed', error=error)
        raise ss_errors._ListingAgentUserError(f'eBay publish failed: {error}', status_code=502, extra={'ebay': data})
    listing_id = str(data.get('listingId') or '').strip() or None
    offer_id = str(data.get('offerId') or '').strip() or None
    return _agent_set_status(proposal_id, 'listed', actor=actor, note=f'listing {listing_id or "?"}', event='published',
                             listing_id=listing_id, offer_id=offer_id)


def _agent_request_photos(proposal_id, *, actor='reviewer', note=''):
    item = _proposal_get(proposal_id, with_events=False)
    if not item:
        raise ss_errors._ListingAgentUserError('proposal not found', status_code=404)
    shots = ', '.join(SHOT_LIST)
    text = f'Listing agent needs photos: {shots}.'
    if note:
        text = f'{text} {note}'
    ss_listing_checks._listagent_set_check_request(upc=item['upc'], take_pictures=True, custom_note=text, source='listing_agent')
    return _agent_set_status(proposal_id, 'needs_photos', actor=actor, note=text, event='photos_requested')


def _agent_reject(proposal_id, *, actor='reviewer', note=''):
    item = _agent_set_status(proposal_id, 'rejected', actor=actor, note=note, event='rejected')
    try:
        ss_listing_queue._listagent_remove_from_queue(item['upc'])
    except Exception:
        pass
    return item


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def listingagent_review_page():
    """Review page for agent proposals: edit, note, approve, hold, request photos, reject."""
    return render_template('listingagent_review.html')


def _agent_error_response(e, context):
    if isinstance(e, ss_errors._ListingAgentUserError):
        payload = {'success': False, 'error': str(e)}
        payload.update(e.extra or {})
        return jsonify(payload), e.status_code
    return jsonify({'success': False, 'error': ss_errors._safe_error(e, context)}), 500


def api_listingagent_proposals_list():
    try:
        status = (request.args.get('status') or 'open').strip().lower()
        limit = ss_listing_settings._listingagent_parse_int(request.args.get('limit'), 200) or 200
        rows, counts = _proposal_list(status=status, limit=limit)
        queued = _agent_queued_candidates(limit=200)
        return jsonify({'success': True, 'items': [_proposal_summary(r) for r in rows], 'counts': counts,
                        'queued_without_proposal': len(queued), 'shot_list': list(SHOT_LIST),
                        'conditions': list(CONDITION_CHOICES)})
    except Exception as e:
        return _agent_error_response(e, 'listingagent:proposals_list')


def api_listingagent_proposal_detail(proposal_id):
    try:
        item = _proposal_get(proposal_id)
        if not item:
            return jsonify({'success': False, 'error': 'proposal not found'}), 404
        item['summary'] = _proposal_summary(item)
        item['problems'] = _agent_validate_for_publish(item.get('proposal') or {}, item.get('sources') or {})
        return jsonify({'success': True, 'item': item})
    except Exception as e:
        return _agent_error_response(e, 'listingagent:proposal_detail')


def api_listingagent_proposals_build():
    """Build one UPC now, or every queued UPC without a proposal in the background."""
    try:
        data = request.get_json(silent=True) or {}
        actor = (data.get('actor') or data.get('reviewed_by') or 'agent').strip() or 'agent'
        base_url = _agent_request_base()
        upc = (data.get('upc') or '').strip()
        if upc:
            item = _agent_build_proposal(upc, actor=actor, base_url=base_url)
            return jsonify({'success': True, 'item': item, 'summary': _proposal_summary(item)})
        with _BUILD_JOB_LOCK:
            if _BUILD_JOB['running']:
                return jsonify({'success': False, 'error': 'A build is already running', 'job': dict(_BUILD_JOB)}), 409
        limit = ss_listing_settings._listingagent_parse_int(data.get('limit'), 25) or 25
        upcs = [r.get('upc') for r in _agent_queued_candidates(limit=limit) if r.get('upc')]
        if not upcs:
            return jsonify({'success': True, 'started': False, 'total': 0, 'message': 'Every queued item already has a proposal.'})
        thread = threading.Thread(target=_agent_build_many, args=(upcs,), kwargs={'actor': actor, 'base_url': base_url}, daemon=True)
        thread.start()
        return jsonify({'success': True, 'started': True, 'total': len(upcs), 'upcs': upcs})
    except Exception as e:
        return _agent_error_response(e, 'listingagent:proposals_build')


def api_listingagent_proposals_build_status():
    with _BUILD_JOB_LOCK:
        return jsonify({'success': True, 'job': json.loads(json.dumps(_BUILD_JOB, default=str))})


def api_listingagent_proposal_save(proposal_id):
    try:
        data = request.get_json(silent=True) or {}
        actor = (data.get('reviewed_by') or data.get('actor') or 'reviewer').strip() or 'reviewer'
        item = _agent_save_review(proposal_id, edits=data.get('edits') or {}, note=data.get('note'), reviewed_by=actor, actor=actor)
        item['problems'] = _agent_validate_for_publish(item.get('proposal') or {}, item.get('sources') or {})
        return jsonify({'success': True, 'item': item})
    except Exception as e:
        return _agent_error_response(e, 'listingagent:proposal_save')


def api_listingagent_proposal_action(proposal_id):
    try:
        data = request.get_json(silent=True) or {}
        action = (data.get('action') or '').strip().lower()
        actor = (data.get('reviewed_by') or data.get('actor') or 'reviewer').strip() or 'reviewer'
        note = (data.get('note') or '').strip()
        edits = data.get('edits') or {}
        if action == 'approve':
            item = _agent_approve(proposal_id, actor=actor, note=note, edits=edits)
        elif action == 'hold':
            if edits or note:
                _agent_save_review(proposal_id, edits=edits, note=note, reviewed_by=actor, actor=actor)
            item = _agent_set_status(proposal_id, 'held', actor=actor, note=note)
        elif action == 'release':
            item = _agent_set_status(proposal_id, 'proposed', actor=actor, note=note, event='released')
        elif action == 'request_photos':
            if edits or note:
                _agent_save_review(proposal_id, edits=edits, note=note, reviewed_by=actor, actor=actor)
            item = _agent_request_photos(proposal_id, actor=actor, note=note)
        elif action == 'reject':
            item = _agent_reject(proposal_id, actor=actor, note=note)
        elif action == 'rebuild':
            current = _proposal_get(proposal_id, with_events=False)
            if not current:
                return jsonify({'success': False, 'error': 'proposal not found'}), 404
            item = _agent_build_proposal(current['upc'], actor=actor, base_url=_agent_request_base())
        else:
            return jsonify({'success': False, 'error': f'unknown action {action!r}'}), 400
        item['problems'] = _agent_validate_for_publish(item.get('proposal') or {}, item.get('sources') or {})
        return jsonify({'success': True, 'item': item, 'summary': _proposal_summary(item)})
    except Exception as e:
        return _agent_error_response(e, 'listingagent:proposal_action')
