"""Every active eBay and Amazon listing accounted for on the warehouse rack.

The Store Listing Helper only raises a "no warehouse" alert after a UPC that
was once in stock drops to zero, so a listing whose code never sat on the rack
is invisible there. This screen walks every active listing instead and files
it under exactly one state:

  accounted    the listing's UPC (exact, zero-padded or a -suffixed unit) is on
               the rack, or someone linked it to a rack row by hand
  suggested    no barcode hit, but the rack holds something whose name, maker
               prefix or sister listing says it is probably this item
  sold_out     the UPC was received at some point (raw BOL, BOL, archived rack
               rows) and is gone now: the listing most likely outlived its stock
  unaccounted  nothing anywhere knows this listing's code or name
  handled      the user dismissed it from this screen

Links are saved through the helper's existing listing_inventory_matches table
so Ready to Ship and the Finder see the same answer.
"""

import copy
import difflib
import hashlib
import json
import re
import sqlite3
import threading
import time
import unicodedata
from finder_aliases import barcode_key
import reconciliation_names as name_matching
import reconciliation_ai as ai_matching
import reconciliation_stock as stock_matching
from collections import defaultdict
from flask import jsonify, redirect, render_template, request
from urllib.parse import urlencode
from . import database as ss_database, errors as ss_errors, listing_alerts as ss_listing_alerts

# dismissed_alerts.alert_type used for listings the user marked handled here.
RECONCILE_ALERT_TYPE = 'reconcile'
CACHE_SECONDS = 30
SUGGESTION_LIMIT = 3
# Below this the best candidate is more likely a sibling product than the item.
SUGGEST_THRESHOLD = 0.55
# At or above this a lookalike outranks "this UPC was received and is gone".
STRONG_SUGGESTION = 0.9
# Tokens this common on the rack say nothing about which item a title means.
RARE_TOKEN_LIMIT = 60

STOP_WORDS = {
    'the', 'a', 'an', 'and', 'of', 'for', 'with', 'in', 'new', 'sealed', 'set', 'pack', 'oz',
    'by', 'to', 'x', 'lot', 'nib', 'nwt', 'brand', 'free', 'shipping', 'inch', 'in.', 'pc',
    'pcs', 'piece', 'size', 'color', 'multi', 'no', 'one',
}

_cache_lock = threading.Lock()
# Held for a whole rebuild (10-40 s on the Pi) so concurrent callers wait for
# one result instead of each rebuilding in parallel.
_build_lock = threading.Lock()
# generation bumps on every invalidation; a payload is current only while
# built_generation still matches it.
_cache_state = {'ts': 0.0, 'payload': None, 'started': 0.0, 'generation': 0, 'built_generation': -1}
# Alert source rows from the last Listings & Stock rebuild. The nav badge and
# the Alerts screen read only this; opening Listings & Stock is the only thing
# that starts a rebuild.
_snapshot_lock = threading.Lock()
_alert_snapshot = {'generated_at': None, 'listings': None, 'unlisted': None}


# ---------------------------------------------------------------- identity --

def _digit_runs(value):
    """Barcode-looking digit runs inside a UPC or SKU field."""
    return re.findall(r'(?<![a-zA-Z0-9])\d{8,14}(?![a-zA-Z0-9])', str(value or ''))


def _canon(code):
    """Digits only, leading zeros dropped: one key for UPC-A, EAN-13 and -suffix bases."""
    text = str(code or '').strip()
    if re.fullmatch(r'\d+\.0+', text):
        text = text.split('.')[0]
    return text.lstrip('0') if text.isdigit() and 8 <= len(text) <= 14 else ''


def _base(barcode):
    text = str(barcode or '').strip()
    match = re.fullmatch(r'(\d{8,14})-\d+', text)
    return match.group(1) if match else text


def _listing_codes(listing):
    """Codes a listing can be matched by, most trustworthy first."""
    codes = []
    for field in (listing.get('upc'), listing.get('sku')):
        for run in _digit_runs(field):
            if run not in codes:
                codes.append(run)
    return codes


def _tokens(text):
    text = unicodedata.normalize('NFKD', str(text or '').casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return {
        word for word in re.findall(r'[a-z0-9]+', text)
        if len(word) > 1 and word not in STOP_WORDS
    }


def _title_key(text):
    return ' '.join(str(text or '').lower().split())


def _maker_prefix(code):
    """A shared six-digit UPC prefix is only a suggestion hint, never identity."""
    canon = _canon(code)
    if not canon or len(canon) > 13:
        return ''
    return canon.zfill(12)[:6]


def listing_hash(store, listing_key):
    """Stable dismissal hash: one per listing, whatever state it is in."""
    data = f'{RECONCILE_ALERT_TYPE}:{str(store or "").lower()}:{str(listing_key or "").lower()}'
    return hashlib.md5(data.encode('utf-8')).hexdigest()


def _safe_int(value, default=1):
    try:
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in ('', 'n/a', 'na', 'null', 'none'):
            return default
        return int(float(text))
    except (TypeError, ValueError, OverflowError):
        return default


# ----------------------------------------------------------------- loading --

def _columns(cur, table):
    return {row[1].lower(): row[1] for row in cur.execute(f'PRAGMA table_info({table})')}


def _load_rack():
    """Rack rows with stock, keyed for every spelling a listing might use."""
    rows = []
    try:
        with ss_database.db_connection('searchRack.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cols = _columns(cur, 'SEARCHRACK')
            if 'barcode' not in cols:
                raise sqlite3.OperationalError('Warehouse barcode column is unavailable')
            optional = {name: cols.get(name) for name in ('title', 'item_position', 'pictureposition',
                                                          'quantity', 'image', 'warehouse_note')}
            select = ['ID AS id', f"{cols['barcode']} AS barcode"]
            for name, actual in optional.items():
                select.append(f"{ss_database._sqlite_ident(actual)} AS {name}" if actual else f"'' AS {name}")
            cur.execute(
                f"SELECT {', '.join(select)} FROM SEARCHRACK"
                f" WHERE {cols['barcode']} IS NOT NULL AND TRIM({cols['barcode']}) != ''"
            )
            for row in cur.fetchall():
                quantity = _safe_int(row['quantity'], 0)
                if quantity <= 0:
                    continue
                barcode = str(row['barcode'] or '').strip()
                location = str(row['item_position'] or '').strip() or str(row['pictureposition'] or '').strip()
                rows.append({
                    'id': int(row['id']),
                    'barcode': barcode,
                    'title': ' '.join(str(row['title'] or '').split()),
                    'location': location,
                    'quantity': quantity,
                    'image': str(row['image'] or '').strip(),
                    'note': str(row['warehouse_note'] or '').strip(),
                })
    except sqlite3.Error:
        raise
    return rows


def _load_catalog():
    """Titles and 'was received' evidence by canonical code, from BOL history."""
    titles = {}
    received = {}
    sources = (
        ('rawbol.db', 'raw_bol_items', 'upc', 'item_description', 'lot_number'),
        ('bol.db', 'bol_items', 'upc', 'item_description', 'lot_number'),
    )
    for database, table, code_col, title_col, lot_col in sources:
        try:
            with ss_database.db_connection(database) as conn:
                conn.row_factory = None
                cols = _columns(conn.cursor(), table)
                if code_col not in cols or title_col not in cols:
                    continue
                lot_expr = ss_database._sqlite_ident(cols[lot_col]) if lot_col in cols else "''"
                sql = (f"SELECT {ss_database._sqlite_ident(cols[code_col])},"
                       f" {ss_database._sqlite_ident(cols[title_col])}, {lot_expr} FROM {table}")
                for code, title, lot in conn.execute(sql):
                    key = _canon(_base(code))
                    if not key:
                        continue
                    title = ' '.join(str(title or '').split())
                    if title and key not in titles:
                        titles[key] = title
                    lot = str(lot or '').strip()
                    if key not in received or (lot and not received[key]):
                        received[key] = lot
        except sqlite3.Error:
            raise
    try:
        with ss_database.db_connection('searchRack.db') as conn:
            conn.row_factory = None
            archived = conn.execute('SELECT barcode FROM archived_searchrack') if 'barcode' in _columns(conn.cursor(), 'archived_searchrack') else []
            for (barcode,) in archived:
                key = _canon(_base(barcode))
                if key:
                    received.setdefault(key, '')
    except sqlite3.Error:
        raise
    return titles, received


def _load_listings():
    """Active eBay listings and active merchant-fulfilled Amazon listings."""
    listings = []
    try:
        with ss_database.db_connection('ebayStore.db') as conn:
            conn.row_factory = sqlite3.Row
            cols = _columns(conn.cursor(), 'INVENTORY')
            sku_expr = 'SKU' if 'sku' in cols else "''"
            url_expr = 'URL' if 'url' in cols else "''"
            image_expr = 'Image' if 'image' in cols else "''"
            rows = conn.execute(
                f"SELECT ID, ItemID, Title, UPC, {sku_expr} AS SKU, Quantity, {image_expr} AS Image,"
                f" {url_expr} AS URL FROM INVENTORY"
                " WHERE LOWER(TRIM(COALESCE(List_State, ''))) = 'active'"
            ).fetchall()
            for row in rows:
                item_id = str(row['ItemID'] or '').strip()
                listing_key = item_id or f"ebay_row:{row['ID']}"
                sku = str(row['SKU'] or '').strip()
                listings.append({
                    'store': 'ebay',
                    'listing_key': listing_key,
                    'listing_id': f'ebay:{listing_key}',
                    'item_id': item_id,
                    'title': ' '.join(str(row['Title'] or '').split()),
                    'upc': str(row['UPC'] or '').strip(),
                    'sku': sku,
                    'qty': max(0, _safe_int(row['Quantity'], 1)),
                    'image': str(row['Image'] or '').strip(),
                    'url': str(row['URL'] or '').strip() or (f'https://www.ebay.com/itm/{item_id}' if item_id else ''),
                    # eBay sellers often park a storage note in the SKU box.
                    'hint': '' if not sku or _digit_runs(sku) or sku.lower() == 'none' else sku,
                })
    except sqlite3.Error:
        raise
    try:
        with ss_database.db_connection('amazonStore.db') as conn:
            conn.row_factory = sqlite3.Row
            cols = _columns(conn.cursor(), 'ITEMS')
            channel_expr = 'FULFILLMENT_CHANNEL' if 'fulfillment_channel' in cols else "''"
            image_expr = 'IMAGE' if 'image' in cols else "''"
            rows = conn.execute(
                f"SELECT ID, ASIN, SKU, TITLE, UPC, QUANTITY, {image_expr} AS IMAGE,"
                f" {channel_expr} AS CHANNEL FROM ITEMS"
                " WHERE LOWER(TRIM(COALESCE(STATUS, ''))) = 'active'"
            ).fetchall()
            for row in rows:
                if str(row['CHANNEL'] or '').strip().upper().startswith('AMAZON'):
                    continue  # FBA stock lives at Amazon, not on the rack
                asin = str(row['ASIN'] or '').strip()
                sku = str(row['SKU'] or '').strip()
                listing_key = sku or asin or f"amazon_row:{row['ID']}"
                listings.append({
                    'store': 'amazon',
                    'listing_key': listing_key,
                    'listing_id': f'amazon:{listing_key}',
                    'item_id': asin,
                    'title': ' '.join(str(row['TITLE'] or '').split()),
                    'upc': str(row['UPC'] or '').strip(),
                    'sku': sku,
                    'qty': max(0, _safe_int(row['QUANTITY'], 1)),
                    'image': str(row['IMAGE'] or '').strip(),
                    'url': f'https://www.amazon.com/dp/{asin}' if asin else '',
                    'hint': '',
                })
    except sqlite3.Error:
        raise
    return listings


def _load_fba_codes():
    """Product keys of active FBA listings: their rack stock is listed, at Amazon."""
    codes = set()
    with ss_database.db_connection('amazonStore.db') as conn:
        cols = _columns(conn.cursor(), 'ITEMS')
        if 'fulfillment_channel' not in cols:
            return codes
        rows = conn.execute(
            "SELECT UPC, ASIN, SKU FROM ITEMS"
            " WHERE LOWER(TRIM(COALESCE(STATUS, ''))) = 'active'"
            " AND UPPER(TRIM(COALESCE(FULFILLMENT_CHANNEL, ''))) LIKE 'AMAZON%'"
        ).fetchall()
    for row in rows:
        for value in row:
            key = _product_key(value)
            if key:
                codes.add(key)
    return codes


def _ensure_alert_tables(conn):
    """The helper creates these relative to the working directory; be safe here."""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS dismissed_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, alert_type TEXT NOT NULL, upc TEXT NOT NULL,
            store TEXT, listing_ids TEXT, dismissed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            snapshot_hash TEXT NOT NULL, UNIQUE(alert_type, snapshot_hash))''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS listing_inventory_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT, store TEXT NOT NULL,
            listing_key TEXT NOT NULL COLLATE NOCASE, listing_id TEXT, marketplace_barcode TEXT,
            listing_title TEXT, searchrack_id INTEGER NOT NULL, inventory_barcode TEXT NOT NULL,
            inventory_location TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(store, listing_key))''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS listing_stock_snapshot (
            id INTEGER PRIMARY KEY CHECK (id = 1), generated_at TEXT NOT NULL, rows_json TEXT NOT NULL)''')


def _load_user_decisions():
    """Manual links keyed (store, listing_key.lower()) and the dismissed hash set."""
    links, dismissed = {}, set()
    try:
        with ss_database.db_connection('listing_alerts.db') as conn:
            conn.row_factory = sqlite3.Row
            _ensure_alert_tables(conn)
            for row in conn.execute('SELECT * FROM listing_inventory_matches'):
                store, key = ss_listing_alerts._normalize_store_listing_identity(row['store'], row['listing_key'])
                if store and key:
                    links[(store, key.lower())] = dict(row)
            for (snapshot_hash,) in conn.execute(
                    'SELECT snapshot_hash FROM dismissed_alerts WHERE alert_type = ?', (RECONCILE_ALERT_TYPE,)):
                dismissed.add(str(snapshot_hash))
    except sqlite3.Error:
        raise
    return links, dismissed


def _load_name_evidence():
    """Saved custom and confirmed names; these never establish stock by themselves."""
    names = defaultdict(list)
    for database, table, code_col, title_col in (
        ('bol.db', 'custom_item_registry', 'upc', 'item_description'),
        ('listing_alerts.db', 'finder_aliases', 'barcode_key', 'title'),
        ('sold.db', 'finder_aliases', 'barcode_key', 'title'),
    ):
        with ss_database.db_connection(database) as conn:
            cols = _columns(conn.cursor(), table)
            if code_col not in cols or title_col not in cols:
                continue
            for code, title in conn.execute(f'SELECT {code_col}, {title_col} FROM {table}'):
                key = barcode_key(code)
                title = ' '.join(str(title or '').split())
                if key and title and title not in names[key]:
                    names[key].append(title)
    return names


# ---------------------------------------------------------------- matching --

class _RackIndex:
    def __init__(self, rows, catalog_titles):
        self.by_id = {row['id']: row for row in rows}
        self.by_exact = defaultdict(list)
        self.by_canon = defaultdict(list)
        self.by_prefix = defaultdict(set)
        self.by_brand = defaultdict(set)
        self.tokens = {}
        self.variants = {}
        self.token_rows = defaultdict(set)
        for row in rows:
            base = _base(row['barcode'])
            canon = _canon(base)
            row['catalog_title'] = catalog_titles.get(canon, '')
            self.variants[row['id']] = list(dict.fromkeys(t for t in [row['title'], *row.get('alternate_titles', []), row['catalog_title']] if t))
            row['display_title'] = next(iter(self.variants[row['id']]), '')
            for title in self.variants[row['id']]:
                for brand in name_matching.attributes(title)['brands']:
                    self.by_brand[brand].add(row['id'])
            self.by_exact[row['barcode'].lower()].append(row)
            if canon:
                self.by_canon[canon].append(row)
            prefix = _maker_prefix(base)
            if prefix:
                self.by_prefix[prefix].add(row['id'])
            words = set().union(*(_tokens(t) for t in self.variants[row['id']]))
            self.tokens[row['id']] = words
            for word in words:
                self.token_rows[word].add(row['id'])
        self.df = {word: len(ids) for word, ids in self.token_rows.items()}

    def barcode_match(self, listing):
        """Rack rows holding this listing's code, and how the spelling differed."""
        for code in _listing_codes(listing):
            rows = self.by_canon.get(_canon(code))
            if rows:
                if all(row['barcode'] == code for row in rows):
                    return rows, 'exact'
                if any('-' in row['barcode'] for row in rows):
                    return rows, 'suffix'
                return rows, 'zero-pad'
        return [], ''

    def _score(self, listing_tokens, listing_title, row):
        if not listing_tokens:
            return 0.0
        best = 0.0
        query_attributes = name_matching.attributes(listing_title)
        for title in self.variants[row['id']]:
            words = _tokens(title)
            if not words:
                continue
            shared = listing_tokens & words
            # Word order, punctuation, and extra marketplace wording should not
            # outweigh the actual product words. Character similarity is secondary.
            coverage = len(shared) / min(len(listing_tokens), len(words))
            jaccard = len(shared) / len(listing_tokens | words)
            ratio = difflib.SequenceMatcher(None, _title_key(listing_title), _title_key(title)).ratio()
            score = max(jaccard, .65 * coverage + .35 * ratio) if len(shared) >= 2 else jaccard
            score, _ = name_matching.weighted_similarity(query_attributes, name_matching.attributes(title), score)
            listing_numbers = set(re.findall(r'\b\d+(?:\.\d+)?\b', listing_title))
            title_numbers = set(re.findall(r'\b\d+(?:\.\d+)?\b', title))
            if listing_numbers and title_numbers and listing_numbers != title_numbers:
                # A different pack count/size must not become strong evidence
                # that overrides the listing's own missing-stock history.
                score = min(score, STRONG_SUGGESTION - .01)
            best = max(best, score)
        return round(best, 3)

    def suggestions(self, listing, limit=SUGGESTION_LIMIT):
        """Distinct product suggestions, best first; None returns all qualifying rows."""
        listing_tokens = _tokens(listing['title'])
        scored = {}
        rare = sorted(listing_tokens, key=lambda word: self.df.get(word, 10 ** 6))[:5]
        candidates = set()
        for word in rare:
            if 0 < self.df.get(word, 0) <= RARE_TOKEN_LIMIT:
                candidates |= self.token_rows[word]
        listing_brands = set(name_matching.attributes(listing['title'])['brands'])
        for brand in listing_brands:
            candidates.update(self.by_brand.get(brand, ()))
        for row_id in candidates:
            row = self.by_id[row_id]
            score = self._score(listing_tokens, listing['title'], row)
            if score >= SUGGEST_THRESHOLD:
                candidate_attributes = name_matching.attributes(row['display_title'])
                _, reasons = name_matching.weighted_similarity(name_matching.attributes(listing['title']), candidate_attributes, score)
                scored[row_id] = (score, ', '.join(reasons) or 'similar product name')
        for code in _listing_codes(listing):
            prefix = _maker_prefix(code)
            for row_id in self.by_prefix.get(prefix, ()):
                if row_id in scored:
                    continue
                shared = listing_tokens & (self.tokens.get(row_id) or set())
                # One shared word under the same maker prefix is just the brand name.
                if len(shared) < 2:
                    continue
                score = self._score(listing_tokens, listing['title'], self.by_id[row_id])
                if not score:
                    continue
                if score < SUGGEST_THRESHOLD:
                    continue
                scored[row_id] = (score, 'same barcode prefix, shares "' + '", "'.join(sorted(shared)[:2]) + '"')
        ranked = sorted(scored.items(), key=lambda item: (-item[1][0], item[0]))
        seen_barcodes = set()
        out = []
        for row_id, (score, reason) in ranked:
            barcode = _product_key(self.by_id[row_id]['barcode'])
            if barcode in seen_barcodes:
                continue  # the same item at another location adds nothing
            seen_barcodes.add(barcode)
            out.append(self._candidate(row_id, score, reason))
            if limit is not None and len(out) >= limit:
                break
        return out

    def _candidate(self, row_id, score, reason):
        row = self.by_id[row_id]
        return {
            'searchrack_id': row['id'], 'barcode': row['barcode'], 'title': row['display_title'],
            'location': row['location'], 'quantity': row['quantity'], 'image': row['image'],
            'score': score, 'reason': reason, 'alternate_titles': row.get('alternate_titles', []),
        }


def _rack_summary(rows):
    return [{'searchrack_id': row['id'], 'barcode': row['barcode'], 'title': row['display_title'],
             'location': row['location'], 'quantity': row['quantity'], 'note': row['note'],
             'alternate_titles': row.get('alternate_titles', [])}
            for row in rows]


def _load_warehouse_index():
    rack = _load_rack()
    catalog_titles, received = _load_catalog()
    name_evidence = _load_name_evidence()
    for row in rack:
        keys = {barcode_key(row['barcode']), barcode_key(_base(row['barcode']))}
        row['alternate_titles'] = sorted({title for key in keys for title in name_evidence.get(key, [])})
    index = _RackIndex(rack, catalog_titles)
    return rack, index, received


def _product_key(barcode):
    base = _base(barcode)
    return _canon(base) or base.lower()


def _all_suggestions(index, listing, rows_by_title):
    merged = {s['searchrack_id']:s for s in index.suggestions(listing, limit=None)}
    for store, sister_rows in rows_by_title.get(_title_key(listing['title']), []):
        if store != listing['store']:
            for row in sister_rows:
                merged[row['id']] = index._candidate(row['id'], .95, f'same title listed on {store}')
    unique = {}
    for suggestion in sorted(merged.values(), key=lambda s:(-s['score'], s['searchrack_id'])):
        unique.setdefault(_product_key(suggestion['barcode']), suggestion)
    return list(unique.values())


def _resolved(listing, index, links, dismissed):
    link = links.get((listing['store'], listing['listing_key'].lower()))
    row = index.by_id.get(_safe_int((link or {}).get('searchrack_id'), 0))
    valid_link = row and row['barcode'].lower() == str(link.get('inventory_barcode') or '').strip().lower()
    return bool(valid_link or index.barcode_match(listing)[0]
                or listing_hash(listing['store'], listing['listing_key']) in dismissed)


def _more_matches(data):
    seen_ids, seen_codes = data.get('seen_ids', []), data.get('seen_barcodes', [])
    if (not isinstance(seen_ids, list) or not isinstance(seen_codes, list)
            or len(seen_ids) > 10000 or len(seen_codes) > 10000
            or any(type(i) is not int or i <= 0 for i in seen_ids)
            or any(not isinstance(c, str) or len(c) > 100 for c in seen_codes)):
        return jsonify({'success':False, 'error':'Invalid previously shown matches'}), 400
    listings = _load_listings()
    listing = next((l for l in listings if l['store'] == data.get('store')
                    and l['listing_key'] == data.get('listing_key')), None)
    if not listing:
        return jsonify({'success':False, 'error':'This listing is no longer active. Refresh the list.'}), 409
    _, index, _ = _load_warehouse_index()
    links, dismissed = _load_user_decisions()
    if _resolved(listing, index, links, dismissed):
        return jsonify({'success':False, 'error':'This listing no longer needs matches. Refresh the list.'}), 409
    rows_by_title = defaultdict(list)
    for other in listings:
        rows, _ = index.barcode_match(other)
        if rows and other['title']:
            rows_by_title[_title_key(other['title'])].append((other['store'], rows))
    seen_products = {_product_key(c) for c in seen_codes}
    seen_ids = set(seen_ids)
    seen_products.update(_product_key(index.by_id[i]['barcode']) for i in seen_ids if i in index.by_id)
    remaining = [s for s in _all_suggestions(index, listing, rows_by_title)
                 if s['searchrack_id'] not in seen_ids and _product_key(s['barcode']) not in seen_products]
    return jsonify({'success':True, 'suggestions':remaining[:SUGGESTION_LIMIT],
                    'has_more_suggestions':len(remaining) > SUGGESTION_LIMIT})


def _apply_ai_result(listing, result, index):
    suggestions = []
    for match in result['matches']:
        row = index.by_id.get(match['searchrack_id'])
        if row is None:
            continue
        score = index._score(_tokens(listing['title']), listing['title'], row)
        candidate = index._candidate(row['id'], score, 'Claude: ' + match['reason'])
        candidate['ai_verdict'] = match['verdict']
        suggestions.append(candidate)
    unique = {}
    for candidate in suggestions + listing['suggestions']:
        unique.setdefault(_product_key(candidate['barcode']), candidate)
    listing['suggestions'] = list(unique.values())
    listing['ai_search'] = {k:result[k] for k in ('checked_at', 'catalog_rows', 'estimated_cost_usd')}
    listing['ai_search']['match_count'] = len(suggestions)


def _stock_context():
    with ss_database.db_connection('sold.db') as conn:
        conn.row_factory = sqlite3.Row
        cols = _columns(conn.cursor(), 'orders')
        if cols and not {'rackupdated', 'barcode', 'quantity'} <= set(cols):
            raise ValueError('Pending sales cannot be read from the orders table')
        orders = [dict(r) for r in conn.execute('SELECT * FROM orders WHERE COALESCE(rackupdated,0)=0')] if cols else []
    with ss_database.db_connection('listing_alerts.db') as conn:
        conn.row_factory = sqlite3.Row
        reviews = [dict(r) for r in conn.execute('SELECT * FROM dismissed_alerts ORDER BY dismissed_at DESC')]
    facebook = set()
    with ss_database.db_connection('bol.db') as conn:
        cols = _columns(conn.cursor(), 'bol_items')
        flags = [f'COALESCE({name},0)>0' for name in ('listed_facebook', 'listed_facebook_qty') if name in cols]
        if 'upc' in cols and flags:
            facebook = {_product_key(r[0]) for r in conn.execute('SELECT upc FROM bol_items WHERE '+' OR '.join(flags)) if r[0]}
    return orders, reviews, facebook


def build_reconciliation():
    """Classify every active listing; pure read of the databases."""
    rack, index, received = _load_warehouse_index()
    listings = _load_listings()
    links, dismissed = _load_user_decisions()

    # A sister listing on the other store can lend its rack rows to a listing
    # that carries no UPC at all, when the titles are the same.
    rows_by_title = defaultdict(list)
    for listing in listings:
        rows, _ = index.barcode_match(listing)
        if rows and listing['title']:
            rows_by_title[_title_key(listing['title'])].append((listing['store'], rows))

    results = []
    totals = defaultdict(lambda: {'listings': 0, 'units': 0})
    for listing in listings:
        entry = dict(listing)
        entry['attributes'] = name_matching.attributes(listing['title'])
        entry['hash'] = listing_hash(listing['store'], listing['listing_key'])
        entry['codes'] = _listing_codes(listing)
        received_lots = [received[_canon(code)] for code in entry['codes'] if _canon(code) in received]
        entry['received'] = bool(received_lots)
        entry['received_lot'] = next((lot for lot in received_lots if lot), '')
        entry['suggestions'] = []
        entry['has_more_suggestions'] = False
        entry['warehouse'] = []
        entry['match_kind'] = ''

        link = links.get((listing['store'], listing['listing_key'].lower()))
        rows, kind = index.barcode_match(listing)
        if link:
            linked_row = index.by_id.get(_safe_int(link.get('searchrack_id'), 0))
            if linked_row and linked_row['barcode'].lower() != str(link.get('inventory_barcode') or '').strip().lower():
                linked_row = None  # SQLite may reuse a deleted row's ID.
            entry['link'] = {'searchrack_id': link.get('searchrack_id'), 'barcode': link.get('inventory_barcode'),
                             'location': link.get('inventory_location'), 'stale': linked_row is None}
        else:
            linked_row = None
        if linked_row:
            entry['state'] = 'accounted'
            entry['match_kind'] = 'linked'
            entry['warehouse'] = _rack_summary([linked_row])
        elif rows:
            entry['state'] = 'accounted'
            entry['match_kind'] = kind
            entry['warehouse'] = _rack_summary(rows)
        elif entry['hash'] in dismissed:
            entry['state'] = 'handled'
        else:
            suggestions = _all_suggestions(index, listing, rows_by_title)
            entry['suggestions'] = suggestions[:SUGGESTION_LIMIT]
            entry['has_more_suggestions'] = len(suggestions) > SUGGESTION_LIMIT
            best = suggestions[0]['score'] if suggestions else 0.0
            # A listing whose own UPC was received and is gone most likely sold
            # out; a lookalike on the rack is then usually a sibling product, so
            # only near-certain evidence pulls it back into "needs a look".
            if suggestions and (not entry['received'] or best >= STRONG_SUGGESTION):
                entry['state'] = 'suggested'
            elif entry['received']:
                entry['state'] = 'sold_out'
            else:
                entry['state'] = 'unaccounted'

        entry['warehouse_qty'] = sum(row['quantity'] for row in entry['warehouse'])
        entry['short_by'] = max(0, entry['qty'] - entry['warehouse_qty']) if entry['state'] == 'accounted' else 0
        totals[entry['state']]['listings'] += 1
        totals[entry['state']]['units'] += entry['qty']
        results.append(entry)

    catalog = ai_matching.Catalog(rack)
    with ss_database.db_connection('listing_alerts.db') as conn:
        for entry in results:
            if entry['state'] not in ('accounted', 'handled'):
                evidence = ai_matching.cached(conn, entry, catalog)
                if evidence:
                    _apply_ai_result(entry, evidence, index)

    orders, reviews, facebook = _stock_context()
    # Stock for an FBA listing is spoken for even though the listing itself is
    # not reconciled here, so it must not surface as "warehouse without listings".
    unlisted, stock_counts = stock_matching.apply(results, rack, orders,
        {r['snapshot_hash'] for r in reviews}, facebook | _load_fba_codes(), _product_key)
    for row in unlisted:
        row['attributes'] = name_matching.attributes(row['title'])
    current_review_hashes = {l['review_hash'] for l in results+unlisted if l['stock_status'] == 'reviewed'}

    order = {'suggested': 0, 'sold_out': 1, 'unaccounted': 2, 'accounted': 3, 'handled': 4}
    results.sort(key=lambda e: (order.get(e['state'], 9), e['store'], e['title'].lower()))
    live = [e for e in results if e['state'] != 'handled']
    accounted = sum(1 for e in live if e['state'] == 'accounted')
    return {
        'success': True,
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'claude_search_available': ai_matching.available(),
        'claude_catalog': catalog.info(),
        'brand_names': name_matching.BRANDS,
        'type_names': {key: sorted(values) for key, values in name_matching.TYPES.items()},
        'color_names': sorted(name_matching.COLORS),
        'stock_counts': stock_counts,
        'unlisted': unlisted,
        'review_history': [r for r in reviews if r['alert_type'] in ('no_warehouse','no_listings','quantity_alert','reconcile','stock_review')
                           and r['snapshot_hash'] not in current_review_hashes],
        'totals': {
            'listings': len(results),
            'live': len(live),
            'accounted': accounted,
            'coverage_pct': round(100.0 * accounted / len(results), 1) if results else 100.0,
            'short': sum(1 for e in live if e['short_by']),
            'by_state': {state: dict(counts) for state, counts in totals.items()},
            'rack_rows': len(rack),
        },
        'listings': results,
    }


# ------------------------------------------------------------------ routes --

def invalidate_cache():
    """Mark the cached payload out of date; the nav badge may keep showing it."""
    with _cache_lock:
        _cache_state['generation'] = int(_cache_state.get('generation') or 0) + 1


def _cache_lookup(built_after=0.0):
    with _cache_lock:
        payload = _cache_state.get('payload')
        if payload is None or float(_cache_state.get('started') or 0.0) < built_after:
            return None
        if _cache_state.get('built_generation') != _cache_state.get('generation'):
            return None
        return payload if time.time() - float(_cache_state.get('ts') or 0.0) < CACHE_SECONDS else None


def _cached_payload(force_refresh):
    """Listings & Stock only: the one place a rebuild may start."""
    requested = time.time()
    if not force_refresh:
        payload = _cache_lookup()
        if payload is not None:
            return payload
    with _build_lock:
        # A rebuild that finished while this caller waited serves it too; a
        # forced refresh only accepts one that started after it asked.
        payload = _cache_lookup(built_after=requested if force_refresh else 0.0)
        if payload is not None:
            return payload
        with _cache_lock:
            generation = _cache_state.get('generation')
        started = time.time()
        payload = build_reconciliation()
        with _cache_lock:
            _cache_state.update(payload=payload, ts=time.time(), started=started, built_generation=generation)
        _save_alert_snapshot(payload)
        return payload


def _save_alert_snapshot(payload):
    """Keep the rows alerts derive from, in memory and on disk so restarts keep them."""
    listings = [l for l in payload.get('listings') or [] if l.get('stock_status') != 'in_stock']
    unlisted = list(payload.get('unlisted') or [])
    generated_at = payload.get('generated_at') or time.strftime('%Y-%m-%dT%H:%M:%S')
    with _snapshot_lock:
        _alert_snapshot.update(generated_at=generated_at, listings=listings, unlisted=unlisted)
    try:
        with ss_database.db_connection('listing_alerts.db') as conn:
            _ensure_alert_tables(conn)
            conn.execute('INSERT OR REPLACE INTO listing_stock_snapshot (id, generated_at, rows_json) VALUES (1, ?, ?)',
                         (generated_at, json.dumps({'listings': listings, 'unlisted': unlisted})))
    except sqlite3.Error as e:
        print(f'Warning: could not save the Listings & Stock alert snapshot: {e}')


def _load_alert_snapshot():
    with _snapshot_lock:
        if _alert_snapshot['listings'] is not None:
            return dict(_alert_snapshot)
    with ss_database.db_connection('listing_alerts.db') as conn:
        _ensure_alert_tables(conn)
        row = conn.execute('SELECT generated_at, rows_json FROM listing_stock_snapshot WHERE id = 1').fetchone()
    if not row:
        return None
    rows = json.loads(row[1] or '{}')
    with _snapshot_lock:
        if _alert_snapshot['listings'] is None:  # a rebuild may have landed meanwhile
            _alert_snapshot.update(generated_at=row[0], listings=rows.get('listings') or [],
                                   unlisted=rows.get('unlisted') or [])
        return dict(_alert_snapshot)


def _alert_status(row, dismissed):
    """Stock status with dismissals made since the snapshot applied, no rebuild."""
    status = row.get('stock_status')
    if row.get('state') == 'handled':
        return status
    base = row.get('reviewed_status') or ('unlisted' if row.get('state') == 'warehouse_unlisted' else status)
    if base in ('in_stock', 'reviewed'):
        return base
    return 'reviewed' if row.get('review_hash') in dismissed else base


def api_listing_reconciliation():
    """Every active listing with its warehouse state, suggestions and totals."""
    if request.method == 'POST':
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or data.get('action') not in ('search_warehouse', 'more_matches'):
            return jsonify({'success': False, 'error': 'A warehouse-search request is required'}), 400
        try:
            if data['action'] == 'more_matches':
                return _more_matches(data)
            listing = next((copy.deepcopy(l) for l in _cached_payload(True)['listings']
                            if l['store'] == data.get('store') and l['listing_key'] == data.get('listing_key')), None)
            if not listing or listing['state'] in ('accounted', 'handled'):
                return jsonify({'success': False, 'error': 'This listing no longer needs a search. Refresh the list.'}), 409
            rack, index, _ = _load_warehouse_index()
            catalog = ai_matching.Catalog(rack)
            with ss_database.db_connection('listing_alerts.db') as conn:
                result, was_cached = ai_matching.search(conn, listing, catalog)
            # Recheck warehouse identity after the network wait; never show a stale/reused row.
            fresh_rack, fresh_index, _ = _load_warehouse_index()
            if ai_matching.Catalog(fresh_rack).digest != catalog.digest:
                return jsonify({'success': False, 'error': 'Warehouse items changed during this search. Please try again.'}), 409
            current = next((l for l in _load_listings() if l['store'] == listing['store']
                            and l['listing_key'] == listing['listing_key']), None)
            links, dismissed = _load_user_decisions()
            link = links.get((listing['store'], listing['listing_key'].lower()))
            linked_row = fresh_index.by_id.get(_safe_int((link or {}).get('searchrack_id'), 0))
            valid_link = linked_row and linked_row['barcode'].lower() == str(link.get('inventory_barcode') or '').strip().lower()
            if (not current or ai_matching.fingerprint(current, catalog) != ai_matching.fingerprint(listing, catalog)
                    or listing['hash'] in dismissed or valid_link or fresh_index.barcode_match(current)[0]):
                return jsonify({'success': False, 'error': 'This listing changed during the search. Refresh the list.'}), 409
            _apply_ai_result(listing, result, fresh_index)
            listing['ai_search']['cached'] = was_cached
            invalidate_cache()
            return jsonify({'success': True, 'suggestions': listing['suggestions'], 'ai_search': listing['ai_search'], 'cached': was_cached})
        except ValueError as e:
            return jsonify({'success': False, 'error': str(e)}), 400
        except Exception as e:
            return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listing-claude-search')}), 500
    force_refresh = str(request.args.get('refresh') or '').strip().lower() in ('1', 'true', 'yes')
    try:
        return jsonify(_cached_payload(force_refresh))
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listing-reconciliation')}), 500


def listing_reconciliation_page():
    """Keep old bookmarks working at the consolidated page."""
    query = urlencode(request.args, doseq=True)
    return redirect('/store-listing-helper' + ('?'+query if query else ''))


def listing_alert_summary(sync_alert=None, counts_only=False):
    """Global Alerts screen and nav badge: the last Listings & Stock result, never a rebuild.

    Only opening Listings & Stock recalculates stock; dismissals made since then
    are applied here. counts_only drops the alert lists for the badge."""
    snapshot = _load_alert_snapshot()
    if snapshot is None:
        return {'success': False, 'not_built': True,
                'error': 'Open Listings & Stock once to calculate stock alerts.'}
    with ss_database.db_connection('listing_alerts.db') as conn:
        _ensure_alert_tables(conn)
        dismissed = {str(row[0]) for row in conn.execute('SELECT snapshot_hash FROM dismissed_alerts')}
    alerts = {k:[] for k in ('no_warehouse','quantity_alert','no_listings','listing_matches','sync_overdue')}
    for listing in snapshot['listings']:
        status = _alert_status(listing, dismissed)
        if status == 'needs_matching':
            alerts['listing_matches'].append(listing)
        if status not in ('out_of_stock','short_stock'):
            continue
        item = dict(upc=listing['upc'], stores=[listing['store']], store=listing['store'],
                    listings=[listing], listing=listing, listing_qty=listing['qty'],
                    warehouse_qty=listing['physical_qty'], pending_sale_qty=listing['pending_sale_qty'],
                    effective_qty=listing['available_qty'], overage=listing['available_short_by'],
                    warehouse_locations=listing['warehouse'], hash=listing['review_hash'], severity='red')
        alerts['no_warehouse' if status == 'out_of_stock' else 'quantity_alert'].append(item)
    for row in snapshot['unlisted']:
        if _alert_status(row, dismissed) != 'unlisted':
            continue
        alerts['no_listings'].append(dict(upc=row['upc'],title=row['title'],image=row['image'],
            warehouse_qty=row['physical_qty'],warehouse_locations=row['warehouse'],hash=row['review_hash'],severity='yellow'))
    if sync_alert and str(sync_alert.get('hash')) not in dismissed:
        alerts['sync_overdue'].append(sync_alert)
    counts = {k:len(v) for k,v in alerts.items()}
    counts['sync_overdue'] = int((alerts['sync_overdue'][0] or {}).get('overdue_count') or 0) if alerts['sync_overdue'] else 0
    counts['total'] = sum(counts[k] for k in ('no_warehouse','quantity_alert','no_listings','sync_overdue'))
    result = {'success':True,'generated_at':snapshot['generated_at'],'counts':counts}
    if not counts_only:
        result['alerts'] = alerts
    return result
