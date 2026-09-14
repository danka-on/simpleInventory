"""Every active eBay and Amazon listing accounted for on the warehouse rack.

The Store Listing Helper only raises a "no warehouse" alert after a UPC that
was once in stock drops to zero, so a listing whose code never sat on the rack
is invisible there. This screen walks every active listing instead and files
it under exactly one state:

  accounted    the listing's UPC (exact, zero-padded or a -suffixed unit) is on
               the rack, or someone linked it to a rack row by hand
  suggested    no barcode hit, but the rack holds something whose name, maker
               prefix or a listing with the same title says it is probably this item
  sold_out     the UPC was received at some point (raw BOL, BOL, archived rack
               rows) and is gone now: the listing most likely outlived its stock
  unaccounted  nothing anywhere knows this listing's code or name
  handled      the user dismissed it from this screen

Links are saved through the helper's existing listing_inventory_matches table
so Ready to Ship and the Finder see the same answer.
"""

import bisect
import copy
import hashlib
import heapq
import json
import math
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
from functools import lru_cache
from flask import jsonify, redirect, render_template, request
from urllib.parse import urlencode
from . import database as ss_database, errors as ss_errors, listing_alerts as ss_listing_alerts

# dismissed_alerts.alert_type used for listings the user marked handled here.
RECONCILE_ALERT_TYPE = 'reconcile'
CACHE_SECONDS = 30
SUGGESTION_LIMIT = 3
# Below this the best candidate is more likely a sibling product than the item.
SUGGEST_THRESHOLD = 0.4
# At or above this a lookalike outranks "this UPC was received and is gone".
STRONG_SUGGESTION = 0.9
# Rows, by summed rarity of the words they share with a listing, that get the full comparison.
SHORTLIST_SIZE = 40
# BOL text is cut at a fixed width ("Salad Pl"), runs words together and abbreviates:
# a rack word that is a prefix or consonant skeleton of a listing word is partial evidence.
PREFIX_CREDIT = .8
ABBREVIATION_CREDIT = .7
MIN_PREFIX = 2
# A cut of 3+ letters reads as the item type the listing spells ("Bow", "PLT"); "Bo" and "Pl" start too many words.
MIN_TYPED_CUT = 3
# Weight of the title-word score against brand/type/color/piece agreement, and
# how much of it comes from explaining the (short, truncated) rack title.
TEXT_WEIGHT = 2.0
RACK_COVERAGE_WEIGHT = .6
# Any listing containing "Tree" fully explains a rack title that is just "Tree";
# titles that short earn only the listing-side share.
MIN_RACK_COVERAGE_TERMS = 2
# Likewise one word the rack uses ("White") cannot explain a listing whose other words it never uses:
# a listing with fewer checkable words earns only their part of the listing-side share.
MIN_CHECKED_TERMS = 2
# Listing words the rack never uses are ignored, so a rack title this short that the rest of the
# listing explains ("Botanic Garden Portmeirion" for its teaspoons) says too little to be certain;
# so does one naming a line word the listing lacks ("Berry" where the listing's own word is "Spice").
SHORT_RACK_TERMS = 3
# Heavier title-word weight lets a Raymond Bowl read like a Raymond Vase, so an
# unrelated item type must cost more than the attribute blend alone charges.
TYPE_CONFLICT_FACTOR = .4
# A word on at most this many rack rows that is not a brand, type, color or size word names a
# product line or model; each title naming one the other lacks marks a sibling of one family.
LINE_WORD_LIMIT = 30
SIBLING_PENALTY = .20
# A borrowed name (see _history_names) may rank a row; only the rack's own names make it strong.
BORROWED_NAME_CAP = STRONG_SUGGESTION - .01
# Another listing, live or ended, with the same _title_key whose own code is on the rack most likely
# is this very item: its rows keep their rank when a listing already has them, and are strong unless
# this listing carries a barcode of its own (_RackIndex.twin_suggestions).
TWIN_SCORE = .95

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


def _plain(text):
    text = str(text or '').casefold()
    if not text.isascii():  # only non-ASCII text carries accents; the per-character pass is most of an index load
        text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
    # 20"x20", 60X84 and 60 x 84 are one size: compare its numbers, not a glued word only one side has.
    return re.sub(r'(?<=\d)["\'\u201d\s]*x\s*(?=\d)|\bx(?=\d)', ' x ', text)


def _words(text):
    return re.findall(r'[a-z0-9]+', _plain(text))


@lru_cache(maxsize=16384)
def _numbers(text):
    # Split like the words, or 4x6 and 5x7 (whose one-digit words are dropped) would show no size to compare.
    return frozenset(re.findall(r'\b\d+(?:\.\d+)?\b', _plain(text)))


def _tokens(text):
    return {word for word in _words(text) if len(word) > 1 and word not in STOP_WORDS}


def _stem(word):
    """One key for singular and plural: Nutcrackers is a Nutcracker, Glasses a Glass."""
    if len(word) > 4 and word.endswith('ies'):
        return word[:-3] + 'y'
    if len(word) > 4 and word.endswith(('sses', 'ches', 'shes', 'xes')):
        return word[:-2]
    if len(word) > 3 and word.endswith('s') and not word.endswith(('ss', 'us', 'is')):
        return word[:-1]
    return word


def _terms(text):
    return {_stem(word) for word in _tokens(text)}


def _is_subsequence(short, word):
    letters = iter(word)
    return all(letter in letters for letter in short)


# Stemmed like title terms; kind, colour, size and filler words never name a product line.
_GENERIC_TERMS = frozenset(_stem(word) for word in name_matching.vocabulary_words() | name_matching.SIZES)


# Marketplace noise around one title: a price, eBay's "1 x" quantity prefix.
_LISTING_NOISE = re.compile(r'^\s*1\s*x\s+|\$\s?\d[\d,]*(?:\.\d+)?', re.IGNORECASE)


# Count words tell a single from a set; the other stop words are marketplace filler.
_TWIN_FILLER = STOP_WORDS - {'set', 'pack', 'pc', 'pcs', 'piece', 'lot'}


def _title_key(text):
    """The words two listings of one item share, in any order, case or punctuation, price aside.

    Sizes and counts are words too, so "Set of 4" is not a single and "Mug Set" is not a mug;
    one letter stays a word, so a monogram or an S/M/L size tells variants apart. Word order
    and repeats are free: a relist that moves "Amber" past "Set of 4" is the same title."""
    return frozenset(word for word in _words(_LISTING_NOISE.sub(' ', str(text or ''))) if word not in _TWIN_FILLER)


def _identity(listing):
    """Every key a store knows this listing by; eBay SKU boxes hold storage notes."""
    keys = {listing.get('listing_key'), listing.get('item_id'), None if listing.get('hint') else listing.get('sku')}
    return {(listing['store'], str(key).strip().lower()) for key in keys if str(key or '').strip().lower() not in ('', 'none')}


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
    """Product keys of active FBA listings, each with a title: their rack stock is listed, at Amazon."""
    codes = {}
    with ss_database.db_connection('amazonStore.db') as conn:
        cols = _columns(conn.cursor(), 'ITEMS')
        if 'fulfillment_channel' not in cols:
            return codes
        rows = conn.execute(
            "SELECT TITLE, UPC, ASIN, SKU FROM ITEMS"
            " WHERE LOWER(TRIM(COALESCE(STATUS, ''))) = 'active'"
            " AND UPPER(TRIM(COALESCE(FULFILLMENT_CHANNEL, ''))) LIKE 'AMAZON%' ORDER BY ID"
        ).fetchall()
    for title, *values in rows:
        for value in values:
            key = _product_key(value)
            if key:
                codes.setdefault(key, title)
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


def _load_store_titles():
    """Every eBay and Amazon listing row, live or ended, FBA included: a listing's code names its product."""
    listings = []
    with ss_database.db_connection('ebayStore.db') as conn:
        conn.row_factory = None
        sku_expr = 'SKU' if 'sku' in _columns(conn.cursor(), 'INVENTORY') else "''"
        for row_id, item_id, title, upc, sku, state in conn.execute(
                f'SELECT ID, ItemID, Title, UPC, {sku_expr}, List_State FROM INVENTORY ORDER BY ID'):
            item_id, sku = str(item_id or '').strip(), str(sku or '').strip()
            listings.append({'store': 'ebay', 'listing_key': item_id or f'ebay_row:{row_id}', 'item_id': item_id,
                             'title': ' '.join(str(title or '').split()), 'upc': str(upc or '').strip(), 'sku': sku,
                             'hint': '' if not sku or _digit_runs(sku) or sku.lower() == 'none' else sku,
                             'live': str(state or '').strip().lower() == 'active'})
    with ss_database.db_connection('amazonStore.db') as conn:
        conn.row_factory = None
        for row_id, asin, sku, title, upc, status in conn.execute('SELECT ID, ASIN, SKU, TITLE, UPC, STATUS FROM ITEMS ORDER BY ID'):
            asin, sku = str(asin or '').strip(), str(sku or '').strip()
            listings.append({'store': 'amazon', 'listing_key': sku or asin or f'amazon_row:{row_id}', 'item_id': asin,
                             'title': ' '.join(str(title or '').split()), 'upc': str(upc or '').strip(), 'sku': sku,
                             'hint': '', 'live': str(status or '').strip().lower() == 'active'})
    return listings


def _history_names(store_titles):
    """Fuller names a product carried on ended eBay listings, with the ItemIDs that carried each.

    Rack titles are often BOL text cut at a fixed width while the marketplace kept the real
    name. These only rank suggestions. eBay keeps an ended copy of a relisted item under the
    same ItemID, so the ids stop a listing being matched by its own words. Sale, return and
    rack-removal titles are not read: they can hold a listing's own title with nothing to tie
    it to that listing, and measured no gain on top of ended listings."""
    names = defaultdict(lambda: defaultdict(set))
    for listing in store_titles:
        item_id = listing['item_id'].lower()
        if listing['store'] == 'ebay' and not listing['live'] and item_id and item_id != 'none' and len(_tokens(listing['title'])) >= 2:
            for key in {_canon(code) for code in _listing_codes(listing)} - {''}:
                names[key][listing['title']].add(item_id)
    return names


# ---------------------------------------------------------------- matching --

# Words that describe rather than name a model; differing on these does not make a sister model.
DESCRIPTIVE_WORDS = {_stem(word) for word in set(name_matching.COLORS).union(*name_matching.TYPES.values())}


class _RackIndex:
    def __init__(self, rows, catalog_titles, history_names=None):
        self.by_id = {row['id']: row for row in rows}
        self.by_exact = defaultdict(list)
        self.by_canon = defaultdict(list)
        self.by_prefix = defaultdict(set)
        self.tokens = {}
        self.variants = {}
        self.variant_terms = {}
        self.term_rows = defaultdict(set)
        self.known_terms = set()
        # (row, name position) of each borrowed name, by every ItemID that carried it.
        self.owned = defaultdict(set)
        for row in rows:
            base = _base(row['barcode'])
            canon = _canon(base)
            row['catalog_title'] = catalog_titles.get(canon, '')
            self.variants[row['id']] = list(dict.fromkeys(t for t in [row['title'], *row.get('alternate_titles', []), row['catalog_title']] if t))
            row['display_title'] = next(iter(self.variants[row['id']]), '')
            known = {name_matching.normalized(title) for title in self.variants[row['id']]}
            borrowed_names = {}
            for title, owners in sorted((history_names or {}).get(_product_key(row['barcode']), {}).items()):
                spelling = name_matching.normalized(title)
                if spelling not in known:
                    borrowed_names.setdefault(spelling, [title, set()])[1].update(owners)
            names = [(title, frozenset(), False) for title in self.variants[row['id']]] + [
                (title, frozenset(owners), True) for title, owners in borrowed_names.values()]
            self.variant_terms[row['id']] = [(title, _terms(title), _numbers(title), owners, borrowed)
                                             for title, owners, borrowed in names]
            for position, (_, terms, _, owners, borrowed) in enumerate(self.variant_terms[row['id']]):
                self.known_terms |= terms
                # Borrowed words stay out of the word index and its rarity: a row's old names
                # rescore it once its own words shortlist it, and never make rack words look common.
                for term in terms if not borrowed else ():
                    self.term_rows[term].add(row['id'])
                for owner in owners:
                    self.owned[owner].add((row['id'], position))
            self.by_exact[row['barcode'].lower()].append(row)
            if canon:
                self.by_canon[canon].append(row)
            prefix = _maker_prefix(base)
            if prefix:
                self.by_prefix[prefix].add(row['id'])
            self.tokens[row['id']] = set().union(*(_tokens(t) for t in self.variants[row['id']]))
        count = max(1, len(rows))
        self.idf = {term: math.log(1 + count / len(ids)) for term, ids in self.term_rows.items()}
        self.unknown_idf = math.log(1 + count)
        self.vocab = sorted(self.idf)
        self.abbreviations = defaultdict(list)
        for term in self.vocab:
            if len(term) >= 3 and term.isalpha() and not set(term[1:]) & set('aeiou'):
                self.abbreviations[term[0]].append(term)
        self._expansion_cache = {}
        self._profiles = {}
        self._query_profiles = {}
        self._cover_cache = {}
        self.twins = defaultdict(list)

    def add_twins(self, listings):
        """File each listing whose own code is on the rack under its _title_key."""
        for listing in listings:
            rows = self.barcode_match(listing)[0]
            key = _title_key(listing['title']) if rows else None
            if key:
                self.twins[key].append((_identity(listing), listing, rows))

    def twin_suggestions(self, listing):
        """Rows held by other listings, live or ended, on either store, with this listing's _title_key.

        A listing with a barcode of its own (not on the rack, or it would be accounted) may be another
        variant sold under the same title: its twins' rows still rank, but below strong, so they never
        outweigh what that barcode's own stock history says."""
        own, found = _identity(listing), {}
        score = BORROWED_NAME_CAP if _listing_codes(listing) else TWIN_SCORE
        # Live listings first: when several lend one row, the reason names a listing that has it now.
        for identity, other, rows in sorted(self.twins.get(_title_key(listing['title']), ()), key=lambda twin: not twin[1]['live']):
            if identity & own:
                continue  # a listing never vouches for itself, nor for its own ended copy
            reason = f"same title as {'a live' if other['live'] else 'an ended'} {STORE_NAMES[other['store']]} listing"
            for row in rows:
                found.setdefault(row['id'], reason)
        return [self._candidate(row_id, score, reason) for row_id, reason in found.items()]

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

    def _expansions(self, term):
        """Rack words that are a cut-short, run-together or abbreviated spelling of term."""
        found = self._expansion_cache.get(term)
        if found is None:
            found = {}
            if term.isalpha() and len(term) >= 4:
                for end in range(MIN_PREFIX, len(term)):
                    if term[:end] in self.idf:
                        found[term[:end]] = PREFIX_CREDIT
                at = bisect.bisect_left(self.vocab, term)
                while at < len(self.vocab) and self.vocab[at].startswith(term):
                    if self.vocab[at] != term:
                        found[self.vocab[at]] = PREFIX_CREDIT
                    at += 1
                for short in self.abbreviations.get(term[0], ()):
                    if short not in found and len(short) < len(term) and _is_subsequence(short, term):
                        found[short] = ABBREVIATION_CREDIT
            self._expansion_cache[term] = found
        return found

    def _text_score(self, query_terms, terms, brand=frozenset(), partial=None):
        """Rarity-weighted share of each title the other explains; rare shared words dominate.

        partial, when given, receives each rack term explained only by a cut-off, run-together or
        abbreviated spelling, mapped to the listing term that explained it."""
        rack_credit = {}
        query_hit = query_total = 0.0
        checked = 0
        # Sorted: when two listing words share one partial rack word the first takes the credit,
        # and set order changes with PYTHONHASHSEED on every restart.
        for term in sorted(query_terms):
            credit = 1.0 if term in terms else 0.0
            if credit:
                rack_credit[term] = 1.0
            else:
                for other, part in self._expansions(term).items():
                    if other in terms and part > rack_credit.get(other, 0.0):
                        rack_credit[other] = part
                        credit = max(credit, part)
                        if partial is not None:
                            partial[other] = term
            # Marketplace words the rack has never used cannot be checked either way.
            if credit or term in self.idf:
                weight = self.idf.get(term, self.unknown_idf)
                query_total += weight
                query_hit += weight * credit
                checked += 1
        # fsum is exact, so a set's iteration order cannot move a score across a threshold.
        rack_total = math.fsum(self.idf.get(term, self.unknown_idf) for term in terms)
        rack_hit = math.fsum(self.idf.get(term, self.unknown_idf) * credit for term, credit in rack_credit.items())
        if partial:
            for term in [term for term in partial if rack_credit[term] >= 1.0]:
                del partial[term]
        if not rack_total or not query_total:
            return 0.0
        # A rack title explained only by shared brand words ("Lenox x No Color") names a maker, not the
        # item: it counts only as far as the less explained of the two titles.
        if brand and rack_credit.keys() <= brand:
            return min(rack_hit / rack_total, query_hit / query_total)
        share = RACK_COVERAGE_WEIGHT if len(terms) >= MIN_RACK_COVERAGE_TERMS else 0.0
        listing_share = query_hit / query_total
        if checked < MIN_CHECKED_TERMS:
            listing_share *= checked / len(query_terms)
        return share * rack_hit / rack_total + (1 - share) * listing_share

    def _cover(self, term):
        """The term and the rack words that spell it cut short or abbreviated; 2-3 letter stubs are too vague to count."""
        found = self._cover_cache.get(term)
        if found is None:
            found = self._cover_cache[term] = frozenset([term, *(other for other, credit in self._expansions(term).items()
                                                                 if len(other) >= 4 or credit == ABBREVIATION_CREDIT)])
        return found

    def _profile(self, title, terms):
        """Brand words, product-line words and the rare ones among them, once per rebuild."""
        profile = self._profiles.get(title)
        if profile is None:
            brand = frozenset(_stem(word) for word in name_matching.brand_words(title))
            line = frozenset(term for term in terms if len(term) >= 4 and term.isalpha()
                             and term not in brand and term not in _GENERIC_TERMS)
            rare = frozenset(term for term in line if 0 < len(self.term_rows.get(term, ())) <= LINE_WORD_LIMIT)
            profile = self._profiles[title] = (brand, line, rare)
        return profile

    def _query_profile(self, title, terms):
        """A listing's profile plus what its words cover on the rack, once per listing."""
        found = self._query_profiles.get(title)
        if found is None:
            brand, _, rare = self._profile(title, terms)
            found = self._query_profiles[title] = (brand, [self._cover(term) for term in sorted(rare)],
                                                   frozenset().union(*map(self._cover, terms)))
        return found

    def _blocked(self, listing):
        """Name positions, by row, of borrowed names that only ever came from this very listing."""
        mine = {str(listing.get('item_id') or '').lower(), listing['listing_key'].lower()}
        blocked = defaultdict(set)
        for owner in mine:
            for row_id, position in self.owned.get(owner, ()):
                if self.variant_terms[row_id][position][3] <= mine:
                    blocked[row_id].add(position)
        return blocked

    def _model_word(self, term, rack_known=True):
        # A listing's long word may be a model name the rack only holds cut short ("Snowcrys").
        return (term.isalpha() and len(term) >= 4 and term not in DESCRIPTIVE_WORDS
                and (term in self.known_terms or (not rack_known and len(term) >= 6)))

    def _sister_model(self, query_terms, terms):
        """A sister model's full name shares the template words and differs in a model word
        each way; it must not stand in for this row."""
        return (any(self._model_word(term, rack_known=False) for term in query_terms - terms)
                and any(self._model_word(term) for term in terms - query_terms))

    def _score(self, listing_tokens, listing_title, row, relatives=None, blocked=()):
        if not listing_tokens:
            return 0.0
        best, relative = 0.0, False
        stems = {word: _stem(word) for word in listing_tokens}
        query_terms = set(stems.values())
        query_attributes = name_matching.attributes(listing_title)
        listing_numbers = _numbers(listing_title)
        query = self._query_profile(listing_title, query_terms)
        unknown = any(len(term) >= 4 and term.isalpha() and term not in self.idf for term in query_terms)
        for position, (title, terms, title_numbers, _, borrowed) in enumerate(self.variant_terms[row['id']]):
            # A name that only ever came from this very listing is not evidence for it.
            if not terms or position in blocked or (borrowed and self._sister_model(query_terms, terms)):
                continue
            profile = self._profile(title, terms)
            partial = {}
            score = self._text_score(query_terms, terms, query[0] | profile[0], partial)
            # Shared words as the listing spells them, so "Knives" still reads as a type word, not a product name.
            shared = [word for word, stem in stems.items() if stem in terms]
            cut = {short: word for short, word in partial.items() if MIN_TYPED_CUT <= len(short) < len(word)}
            spelled = tuple(sorted({(word, cut[_stem(word)]) for word in _words(title) if _stem(word) in cut})) if cut else ()
            score, _ = name_matching.weighted_similarity(query_attributes, name_matching.attributes(title, spelled), score,
                                                         TEXT_WEIGHT, TYPE_CONFLICT_FACTOR,
                                                         len(name_matching.name_words(shared)))
            if listing_numbers and title_numbers and listing_numbers != title_numbers:
                # A different pack count/size must not become strong evidence
                # that overrides the listing's own missing-stock history.
                score = min(score, STRONG_SUGGESTION - .01)
            if unknown and (len(terms) <= SHORT_RACK_TERMS or not profile[2] <= query[2]):
                score = min(score, STRONG_SUGGESTION - .01)
            score, is_relative = self._sibling(score, query, terms, profile)
            if borrowed:
                score = min(score, BORROWED_NAME_CAP)
            if score > best or (score == best and not is_relative):
                best, relative = score, is_relative
        if relative and relatives is not None:
            relatives.add(row['id'])
        return round(best, 3)

    def _sibling(self, score, query, terms, profile):
        """Score after the sibling check, and whether the row is only a relative.

        Siblings of one family share brand, type and most words; the rare line word each
        names and the other cannot spell (Varnel vs Quellbrook) tells them apart.
        """
        if not (query[1] and profile[2]) or profile[2] <= query[2] \
                or not any(cover.isdisjoint(terms) for cover in query[1]):
            return score, False
        penalized = score * (1 - SIBLING_PENALTY)
        if penalized < SUGGEST_THRESHOLD <= score:
            # Still listed below the lookalikes; one sharing no line word only fills an otherwise empty list.
            return SUGGEST_THRESHOLD, profile[1].isdisjoint(query[2])
        return min(penalized, STRONG_SUGGESTION - .01), False

    def _shortlist(self, query_terms, claims=None, own=frozenset()):
        """Rows sharing the rarest words, from one pass over the word index.

        With claims (_claimed_rows), rows a listing other than own already has rank after
        every free row, so they must not push a free row out of the full comparison: the
        best SHORTLIST_SIZE free rows join the usual cut, which still keeps its claimed rows."""
        weights = defaultdict(float)
        # Sorted so float sums, and with them the cut at SHORTLIST_SIZE, are the same on every restart.
        for term in sorted(query_terms):
            matches = dict(self._expansions(term))
            if term in self.idf:
                matches[term] = 1.0
            for other, credit in matches.items():
                weight = credit * min(self.idf[other], self.idf.get(term, self.unknown_idf))
                for row_id in self.term_rows[other]:
                    weights[row_id] += weight
        key = lambda row_id: (weights[row_id], -row_id)
        top = heapq.nlargest(SHORTLIST_SIZE, weights, key=key)
        taken = {row_id for row_id in weights.keys() & (claims or {}).keys() if not claims[row_id].keys() <= own}
        if taken.intersection(top):
            top = list(dict.fromkeys(top + heapq.nlargest(SHORTLIST_SIZE, weights.keys() - taken, key=key)))
        return top

    def suggestions(self, listing, limit=SUGGESTION_LIMIT, distinct=True, claims=None, twins=frozenset()):
        """Distinct product suggestions, best first; None returns all qualifying rows.

        distinct=False keeps every location of a product, so a caller can pick one;
        claims (_claimed_rows) gives free rows their own shortlist places (_shortlist);
        twins are claimed rows _all_suggestions keeps at their rank."""
        listing_tokens = _tokens(listing['title'])
        scored, relatives = {}, set()
        own = _owner_ids(listing)
        blocked = self._blocked(listing)
        candidates = self._shortlist({_stem(word) for word in listing_tokens}, claims, own)
        for row_id in candidates:
            row = self.by_id[row_id]
            score = self._score(listing_tokens, listing['title'], row, relatives, blocked.get(row_id, ()))
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
                score = self._score(listing_tokens, listing['title'], self.by_id[row_id], relatives, blocked.get(row_id, ()))
                if not score:
                    continue
                if score < SUGGEST_THRESHOLD:
                    continue
                scored[row_id] = (score, 'same barcode prefix, shares "' + '", "'.join(sorted(shared)[:2]) + '"')
        # A relative (another line of the family, sharing no line word) only fills an otherwise empty list.
        # Rows other listings have follow every free row, so only a free row or a twin empties it of
        # relatives; dropping them for a claimed row would just put that row first.
        if relatives and (twins or any(not (claims or {}).get(row_id, {}).keys() - own for row_id in scored.keys() - relatives)):
            for row_id in relatives:
                scored.pop(row_id, None)
        ranked = sorted(scored.items(), key=lambda item: (-item[1][0], item[0]))
        seen_barcodes = set()
        out = []
        for row_id, (score, reason) in ranked:
            barcode = _product_key(self.by_id[row_id]['barcode'])
            if distinct and barcode in seen_barcodes:
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
    # Only warehouse and BOL wording teaches brands: never listing titles or saved aliases.
    name_matching.learn_brands([row['title'] for row in rack] + list(catalog_titles.values()))
    store_titles = _load_store_titles()
    index = _RackIndex(rack, catalog_titles, _history_names(store_titles))
    index.add_twins(store_titles)
    return rack, index, received


def _product_key(barcode):
    base = _base(barcode)
    return _canon(base) or base.lower()


STORE_NAMES = {'ebay': 'eBay', 'amazon': 'Amazon', 'fba': 'Amazon FBA'}


def _listing_label(store, title):
    title = ' '.join(str(title or '').split())
    title = title if len(title) <= 60 else title[:59].rstrip() + '…'
    return f'{STORE_NAMES.get(store, store)} listing' + (f' "{title}"' if title else '')


def _owner_ids(listing):
    """The claim owners that are this listing itself: its UPC match and its hand link."""
    return {listing['listing_id'], f"{listing['store']}:{listing['listing_key'].lower()}"}


def _claimed_rows(listings, index, links, matches=None, fba_codes=None):
    """Rack rows an active listing already has: {row_id: {owner_id: label}}.

    Owners: listing_id for a UPC match, 'store:listing_key' for a hand link,
    'fba:<product key>' for FBA stock. A rebuild passes the barcode matches
    (listing order) and FBA codes it already loaded instead of redoing them."""
    if matches is None:
        matches = [index.barcode_match(listing) for listing in listings]
    claims = defaultdict(dict)
    active = {}
    for listing, (rows, _) in zip(listings, matches):
        active[(listing['store'], listing['listing_key'].lower())] = listing
        for row in rows:
            claims[row['id']][listing['listing_id']] = _listing_label(listing['store'], listing['title'])
    for (store, key), link in links.items():
        listing = active.get((store, key))
        row = index.by_id.get(_safe_int(link.get('searchrack_id'), 0))
        # An ended listing's link holds no stock, and SQLite may reuse a deleted row's ID.
        if not listing or not row or row['barcode'].lower() != str(link.get('inventory_barcode') or '').strip().lower():
            continue
        if listing['listing_id'] not in claims[row['id']]:
            claims[row['id']][f'{store}:{key}'] = 'hand-linked to ' + _listing_label(store, listing['title'])
    fba_codes = _load_fba_codes() if fba_codes is None else fba_codes
    for row in index.by_id.values() if fba_codes else ():
        key = _product_key(row['barcode'])
        if key in fba_codes:
            # Named like any other listing: the title shows whether that stock is this very product.
            claims[row['id']][f'fba:{key}'] = _listing_label('fba', fba_codes[key])
    return dict(claims)


def _claim_label(claims, listing, row_id):
    """Other active listings that already have a rack row, as one short line ('' if none).

    The query listing's own UPC match and hand link never count against it."""
    mine = _owner_ids(listing)
    labels = list(dict.fromkeys(label for owner, label in (claims or {}).get(row_id, {}).items() if owner not in mine))
    # Relists, the other store, FBA and hand links can pile onto one row; two names say enough.
    return '; '.join(labels[:2]) + (f'; +{len(labels) - 2} more' if len(labels) > 2 else '')


def _all_suggestions(index, listing, claims=None):
    """One suggestion per product, best first.

    With claims (_claimed_rows), rows another listing already has follow every free
    candidate, order kept inside each group, labelled claimed_by. They are never
    dropped: relists, duplicates and the same product on the other store share rows.
    An exact twin keeps its rank (still labelled): a row lent by another listing, live or
    ended, on either store, with the same _title_key is most likely this very item; a near
    title ("Set of 4" added) lends nothing."""
    lent = index.twin_suggestions(listing)
    twins = {twin['searchrack_id'] for twin in lent}
    locations = defaultdict(list)  # every qualifying row of a product, best first
    for suggestion in index.suggestions(listing, limit=None, distinct=False, claims=claims, twins=twins):
        locations[_product_key(suggestion['barcode'])].append(suggestion)
    merged = {rows[0]['searchrack_id']: rows[0] for rows in locations.values()}
    for twin in lent:
        # The twin says why a row another listing has keeps its rank; a closer name keeps its score.
        named = merged.get(twin['searchrack_id'], twin)
        merged[twin['searchrack_id']] = dict(twin, score=max(twin['score'], named['score']))
    unique = {}
    for suggestion in sorted(merged.values(), key=lambda s:(-s['score'], s['searchrack_id'])):
        unique.setdefault(_product_key(suggestion['barcode']), suggestion)
    if claims is None:
        return list(unique.values())
    free, claimed = [], []
    for key, suggestion in unique.items():
        label = _claim_label(claims, listing, suggestion['searchrack_id'])
        spare = next((s for s in locations.get(key, ()) if not _claim_label(claims, listing, s['searchrack_id'])), None) if label else None
        if spare:
            # A hand link holds one row, so another location of the same item can be free:
            # offer that unit among the free candidates with the product's score (state never moves).
            free.append(dict(spare, score=suggestion['score']))
        elif label:
            (free if suggestion['searchrack_id'] in twins else claimed).append(dict(suggestion, claimed_by=label))
        else:
            free.append(suggestion)
    return free + claimed


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
    # Same claims as the rebuild, so rows other listings have stay last on every page.
    claims = _claimed_rows(listings, index, links)
    seen_products = {_product_key(c) for c in seen_codes}
    seen_ids = set(seen_ids)
    seen_products.update(_product_key(index.by_id[i]['barcode']) for i in seen_ids if i in index.by_id)
    remaining = [s for s in _all_suggestions(index, listing, claims=claims)
                 if s['searchrack_id'] not in seen_ids and _product_key(s['barcode']) not in seen_products]
    return jsonify({'success':True, 'suggestions':remaining[:SUGGESTION_LIMIT],
                    'has_more_suggestions':len(remaining) > SUGGESTION_LIMIT})


def _apply_ai_result(listing, result, index, claims=None):
    suggestions = []
    for match in result['matches']:
        row = index.by_id.get(match['searchrack_id'])
        if row is None:
            continue
        score = index._score(_tokens(listing['title']), listing['title'], row, blocked=index._blocked(listing).get(row['id'], ()))
        candidate = index._candidate(row['id'], score, 'Claude: ' + match['reason'])
        candidate['ai_verdict'] = match['verdict']
        # Claude's order stands, but a row another listing already has must say so before a link.
        label = _claim_label(claims, listing, row['id'])
        if label:
            candidate['claimed_by'] = label
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

    matches = [index.barcode_match(listing) for listing in listings]
    fba_codes = _load_fba_codes()
    # Rows another active listing already has stay suggestible but rank last.
    claims = _claimed_rows(listings, index, links, matches, fba_codes)

    results = []
    totals = defaultdict(lambda: {'listings': 0, 'units': 0})
    for listing, (rows, kind) in zip(listings, matches):
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
            suggestions = _all_suggestions(index, listing, claims=claims)
            entry['suggestions'] = suggestions[:SUGGESTION_LIMIT]
            entry['has_more_suggestions'] = len(suggestions) > SUGGESTION_LIMIT
            # Claimed rows rank last, so the strongest evidence can sit anywhere.
            best = max((s['score'] for s in suggestions), default=0.0)
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
                    _apply_ai_result(entry, evidence, index, claims)

    orders, reviews, facebook = _stock_context()
    # Stock for an FBA listing is spoken for even though the listing itself is
    # not reconciled here, so it must not surface as "warehouse without listings".
    unlisted, stock_counts = stock_matching.apply(results, rack, orders,
        {r['snapshot_hash'] for r in reviews}, facebook | set(fba_codes), _product_key)
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
        'brand_names': name_matching.brand_names(),
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
            fresh_listings = _load_listings()
            current = next((l for l in fresh_listings if l['store'] == listing['store']
                            and l['listing_key'] == listing['listing_key']), None)
            links, dismissed = _load_user_decisions()
            link = links.get((listing['store'], listing['listing_key'].lower()))
            linked_row = fresh_index.by_id.get(_safe_int((link or {}).get('searchrack_id'), 0))
            valid_link = linked_row and linked_row['barcode'].lower() == str(link.get('inventory_barcode') or '').strip().lower()
            if (not current or ai_matching.fingerprint(current, catalog) != ai_matching.fingerprint(listing, catalog)
                    or listing['hash'] in dismissed or valid_link or fresh_index.barcode_match(current)[0]):
                return jsonify({'success': False, 'error': 'This listing changed during the search. Refresh the list.'}), 409
            _apply_ai_result(listing, result, fresh_index, _claimed_rows(fresh_listings, fresh_index, links))
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
