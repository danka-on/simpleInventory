"""Sweet Shelves Lister: the server side of the Chrome listing side panel.

The extension opens beside an eBay or Amazon Seller Central listing page, shows the listing
proposals that were selected for the browser, fills what it can on the store page, and, once
the listing is live, confirms it here. Confirming writes the store listing <-> warehouse link
(listing_links, the listing queue, the store-listing fallback match and a Finder alias), so
Ready to Ship and the warehouse pages resolve the order even when the store shows a different
UPC than the one on our rack.

This module is shared verbatim by the modular app (sweetshelves/routing.py) and the Desktop
debby app.py, so every dependency is injected through register(app, deps).
"""

import datetime
import html as html_module
import json
import os
import re
import sqlite3
from pathlib import Path

from flask import jsonify, request, send_from_directory

VERSION = '0.1.0'
PLATFORMS = ('ebay', 'amazon')
OPEN_STATUSES = ('proposed', 'held', 'needs_photos', 'blocked')
MUTATION_HEADER = 'X-Sweet-Shelves-Lister'
FEED_REQUIRED = ('manifest.json', 'background.js', 'sidepanel.html')

# eBay inventory condition enum -> Amazon condition_type used by the Listings Items API.
AMAZON_CONDITIONS = {
    'NEW': 'new_new',
    'NEW_OTHER': 'new_open_box',
    'NEW_WITH_DEFECTS': 'new_open_box',
    'USED_EXCELLENT': 'used_like_new',
    'USED_VERY_GOOD': 'used_very_good',
    'USED_GOOD': 'used_good',
    'USED_ACCEPTABLE': 'used_acceptable',
}

_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'[ \t]+')
_EBAY_ITEM_RE = re.compile(r'^\d{9,15}$')
_ASIN_RE = re.compile(r'^[A-Z0-9]{10}$')


class ListerError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.datetime.now().isoformat(timespec='seconds')


def _loads(text, default):
    if text in (None, ''):
        return default
    try:
        value = json.loads(text)
    except Exception:
        return default
    return value if value is not None else default


def _order_key(stamp):
    """ISO timestamps sort as text; turn one into a number so newest-first sorting needs no parsing."""
    digits = re.sub(r'\D', '', str(stamp or ''))[:14]
    return int(digits) if digits else 0


def _row(row):
    if row is None:
        return None
    try:
        return {key: row[key] for key in row.keys()}
    except Exception:
        return dict(row)


def _text(value, limit=None):
    text = str(value if value is not None else '').strip()
    return text[:limit] if limit else text


def html_to_text(value):
    """Plain text for stores that reject HTML (Amazon condition notes, eBay title fields)."""
    text = str(value or '')
    text = re.sub(r'(?i)<\s*br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</\s*(p|div|h[1-6]|tr|ul|ol|table)\s*>', '\n\n', text)
    text = re.sub(r'(?i)</\s*li\s*>', '\n', text)
    text = _TAG_RE.sub('', text)
    text = html_module.unescape(text)
    text = _WS_RE.sub(' ', text)
    return re.sub(r'\n\s*\n+', '\n\n', text).strip()


def _absolute(url, base_url):
    value = _text(url)
    if not value:
        return ''
    if value.startswith('/'):
        return base_url.rstrip('/') + value
    return value


def fill_fields(proposal, eligibility, *, upc, base_url):
    """The values the side panel fills into a store page, derived from one proposal."""
    proposal = proposal or {}
    eligibility = eligibility or {}
    images = []
    for image in proposal.get('images') or []:
        if isinstance(image, dict):
            if not image.get('enabled', True):
                continue
            url = _absolute(image.get('url'), base_url)
        else:
            url = _absolute(image, base_url)
        if url and url not in images:
            images.append(url)
    aspects = {}
    for name, value in (proposal.get('aspects') or {}).items():
        if isinstance(value, (list, tuple)):
            cleaned = [_text(v) for v in value if _text(v)]
            if cleaned:
                aspects[_text(name)] = cleaned
        elif _text(value):
            aspects[_text(name)] = [_text(value)]
    brand = ''
    for name, values in aspects.items():
        if name.lower() == 'brand' and values:
            brand = values[0]
    condition = _text(proposal.get('condition')).upper()
    description_html = _text(proposal.get('listingDescription'))
    quantity = proposal.get('quantity')
    listable = eligibility.get('listable_quantity')
    try:
        quantity = int(quantity) if quantity not in (None, '') else None
    except (TypeError, ValueError):
        quantity = None
    if listable not in (None, '') and quantity is not None:
        try:
            quantity = min(quantity, int(listable))
        except (TypeError, ValueError):
            pass
    return {
        'upc': upc,
        'sku': _text(proposal.get('sku')) or upc,
        'title': _text(proposal.get('title'), 80),
        'price': proposal.get('price'),
        'currency': _text(proposal.get('currency')) or 'USD',
        'quantity': quantity,
        'condition': condition,
        'amazonCondition': AMAZON_CONDITIONS.get(condition, ''),
        'conditionDescription': _text(proposal.get('conditionDescription')),
        'descriptionHtml': description_html,
        'descriptionText': html_to_text(description_html),
        'categoryId': _text(proposal.get('categoryId')),
        'categoryPath': _text(proposal.get('categoryPath')),
        'brand': brand,
        'aspects': aspects,
        'images': images[:12],
        'lots': list((eligibility.get('prep') or {}).get('lots') or []),
        'racks': list((eligibility.get('rack') or {}).get('locations') or []),
        'listableQuantity': listable,
    }


def validate_link(data):
    """Normalise and check a confirm-link body. Returns the cleaned dict or raises ListerError."""
    platform = _text(data.get('platform')).lower()
    if platform not in PLATFORMS:
        raise ListerError('platform must be ebay or amazon')
    listing_id = _text(data.get('listing_id') or data.get('listingId'), 64)
    sku = _text(data.get('sku'), 120)
    asin = _text(data.get('asin'), 20).upper()
    if platform == 'ebay':
        if not _EBAY_ITEM_RE.match(listing_id):
            raise ListerError('eBay needs the numeric item number from the live listing')
        asin = ''
    else:
        if asin and not _ASIN_RE.match(asin):
            raise ListerError('ASIN must be 10 letters or digits')
        if not sku and not asin:
            raise ListerError('Amazon needs the seller SKU or the ASIN')
        listing_id = listing_id or asin
    price = data.get('price')
    try:
        price = round(float(price), 2) if price not in (None, '') else None
    except (TypeError, ValueError):
        raise ListerError('price must be a number')
    quantity = data.get('quantity')
    try:
        quantity = int(quantity) if quantity not in (None, '') else None
    except (TypeError, ValueError):
        raise ListerError('quantity must be a whole number')
    url = _text(data.get('url'), 500)
    if url and not url.lower().startswith('https://'):
        raise ListerError('url must be an https link')
    return {
        'platform': platform,
        'listing_id': listing_id,
        'offer_id': _text(data.get('offer_id') or data.get('offerId'), 64),
        'sku': sku,
        'asin': asin,
        'store_upc': _text(data.get('store_upc') or data.get('storeUpc'), 40),
        'url': url,
        'title': _text(data.get('title'), 200),
        'price': price,
        'quantity': quantity,
        'note': _text(data.get('note'), 500),
    }


class Lister:
    def __init__(self, deps, static_folder):
        self.db = deps['db_connection']
        self.safe_error = deps['_safe_error']
        self.mark_listed = deps['_listagent_mark_listed']
        self.upc_variants = deps['_listagent_upc_variants']
        self.format_upc12 = deps['_listagent_format_upc12']
        self.init_listagent = deps['_listagent_init_tables']
        self.clear_scan_cache = deps.get('_listing_helper_scan_cache_clear')
        self.feed_dir = Path(static_folder) / 'lister'

    # -- storage -----------------------------------------------------------------------------

    @staticmethod
    def init_tables(cur):
        cur.execute('''
            CREATE TABLE IF NOT EXISTS listing_helper_selections (
                proposal_id INTEGER PRIMARY KEY,
                upc TEXT NOT NULL,
                platform_hint TEXT,
                selected_by TEXT,
                selected_at TEXT NOT NULL
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS listing_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upc TEXT NOT NULL,
                proposal_id INTEGER,
                platform TEXT NOT NULL,
                listing_id TEXT,
                offer_id TEXT,
                sku TEXT,
                asin TEXT,
                store_upc TEXT,
                url TEXT,
                title TEXT,
                price REAL,
                quantity INTEGER,
                note TEXT,
                effects_json TEXT,
                created_by TEXT,
                source TEXT NOT NULL DEFAULT 'extension',
                created_at TEXT NOT NULL
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_links_upc ON listing_links(upc)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_links_platform_listing ON listing_links(platform, listing_id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_links_platform_sku ON listing_links(platform, sku)')

    @staticmethod
    def _has_table(cur, name):
        cur.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,))
        return cur.fetchone() is not None

    def _variants(self, upc):
        variants = [v for v in (self.upc_variants(upc) or []) if v]
        return variants or [upc]

    # -- proposals -------------------------------------------------------------------------

    def items(self, *, scope='selected', platform='', base_url='http://localhost/'):
        scope = (scope or 'selected').strip().lower()
        if scope not in ('selected', 'open', 'all'):
            raise ListerError('scope must be selected, open or all')
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            cur.execute('SELECT * FROM listing_helper_selections')
            selections = {int(r['proposal_id']): _row(r) for r in cur.fetchall()}
            rows = []
            if self._has_table(cur, 'listing_proposals'):
                if scope == 'selected':
                    if selections:
                        placeholders = ','.join('?' for _ in selections)
                        cur.execute(f'SELECT * FROM listing_proposals WHERE id IN ({placeholders}) ORDER BY updated_at DESC, id DESC',
                                    tuple(selections))
                        rows = cur.fetchall()
                elif scope == 'open':
                    placeholders = ','.join('?' for _ in OPEN_STATUSES)
                    cur.execute(f'SELECT * FROM listing_proposals WHERE status IN ({placeholders}) ORDER BY updated_at DESC, id DESC LIMIT 300',
                                OPEN_STATUSES)
                    rows = cur.fetchall()
                else:
                    cur.execute('SELECT * FROM listing_proposals ORDER BY updated_at DESC, id DESC LIMIT 300')
                    rows = cur.fetchall()
            proposals = [_row(r) for r in rows]
            upcs = [p['upc'] for p in proposals]
            links = self._links_for_upcs(cur, upcs)
        items = []
        # Store lookups cost two database opens per item; keep them for the short selected list.
        with_stores = scope == 'selected' or len(proposals) <= 60
        for proposal in proposals:
            item = self._item(proposal, selections.get(int(proposal['id'])), links.get(proposal['upc'], []), base_url,
                              with_stores=with_stores)
            if platform and platform in PLATFORMS and any(l['platform'] == platform for l in item['links']):
                item['listedOnPlatform'] = True
            items.append(item)
        # Selected items first (most recently selected on top), then the rest by update time.
        items.sort(key=lambda it: (0 if it['selected'] else 1, it['selectedAt'] and -_order_key(it['selectedAt']) or 0,
                                   -_order_key(it['updatedAt'])))
        counts = {'selected': len(selections), 'returned': len(items)}
        return items, counts

    def _links_for_upcs(self, cur, upcs):
        out = {}
        if not upcs:
            return out
        placeholders = ','.join('?' for _ in upcs)
        cur.execute(f'SELECT * FROM listing_links WHERE upc IN ({placeholders}) ORDER BY id DESC', tuple(upcs))
        for row in cur.fetchall():
            link = self._link_public(_row(row))
            out.setdefault(link['upc'], []).append(link)
        return out

    def _item(self, proposal, selection, links, base_url, *, with_stores=True):
        data = _loads(proposal.get('proposal_json'), {})
        eligibility = _loads(proposal.get('eligibility_json'), {})
        flags = _loads(proposal.get('flags_json'), [])
        upc = proposal['upc']
        fields = fill_fields(data, eligibility, upc=upc, base_url=base_url)
        stores = self._store_listings(upc) if with_stores else {'ebay': [], 'amazon': []}
        return {
            'id': int(proposal['id']),
            'upc': upc,
            'status': proposal.get('status'),
            'selected': selection is not None,
            'selectedAt': (selection or {}).get('selected_at') or '',
            'selectedBy': (selection or {}).get('selected_by') or '',
            'platformHint': (selection or {}).get('platform_hint') or '',
            'updatedAt': proposal.get('updated_at') or '',
            'reviewerNote': proposal.get('reviewer_note') or '',
            'flags': [f for f in flags if isinstance(f, dict)],
            'fields': fields,
            'thumb': fields['images'][0] if fields['images'] else '',
            'existing': stores,
            'links': links,
            'listedListingId': proposal.get('listed_listing_id') or '',
        }

    def _store_listings(self, upc):
        """Listings the stores already carry for this UPC, so the panel can warn before a duplicate."""
        variants = self._variants(upc)
        base = variants[0].split('-', 1)[0]
        keys = {v for v in variants} | {base, base.lstrip('0')}
        keys = tuple(k for k in keys if k)
        placeholders = ','.join('?' for _ in keys)
        out = {'ebay': [], 'amazon': []}
        try:
            with self.db('ebayStore.db') as conn:
                cur = conn.cursor()
                if self._has_table(cur, 'INVENTORY'):
                    cur.execute(f"SELECT ItemID, SKU, UPC, Title, List_State FROM INVENTORY WHERE TRIM(COALESCE(UPC, '')) IN ({placeholders}) ORDER BY ID DESC LIMIT 10", keys)
                    for r in cur.fetchall():
                        r = _row(r)
                        out['ebay'].append({'listingId': _text(r.get('ItemID')), 'sku': _text(r.get('SKU')),
                                            'upc': _text(r.get('UPC')), 'title': _text(r.get('Title')),
                                            'state': _text(r.get('List_State'))})
        except sqlite3.Error:
            pass
        try:
            with self.db('amazonStore.db') as conn:
                cur = conn.cursor()
                if self._has_table(cur, 'ITEMS'):
                    cur.execute(f"SELECT ASIN, SKU, UPC, TITLE, STATUS, FULFILLMENT_CHANNEL FROM ITEMS WHERE TRIM(COALESCE(UPC, '')) IN ({placeholders}) ORDER BY rowid DESC LIMIT 10", keys)
                    for r in cur.fetchall():
                        r = _row(r)
                        out['amazon'].append({'asin': _text(r.get('ASIN')), 'sku': _text(r.get('SKU')),
                                              'upc': _text(r.get('UPC')), 'title': _text(r.get('TITLE')),
                                              'state': _text(r.get('STATUS')),
                                              'fulfillment': _text(r.get('FULFILLMENT_CHANNEL'))})
        except sqlite3.Error:
            pass
        return out

    def select(self, proposal_ids, *, selected=True, actor='', platform_hint=''):
        ids = []
        for value in proposal_ids or []:
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                raise ListerError('proposal_ids must be integers')
        if not ids:
            raise ListerError('proposal_ids is required')
        platform_hint = platform_hint if platform_hint in PLATFORMS else ''
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            if not selected:
                cur.execute(f"DELETE FROM listing_helper_selections WHERE proposal_id IN ({','.join('?' for _ in ids)})", tuple(ids))
            else:
                if not self._has_table(cur, 'listing_proposals'):
                    raise ListerError('No listing proposals exist yet', 404)
                cur.execute(f"SELECT id, upc, status FROM listing_proposals WHERE id IN ({','.join('?' for _ in ids)})", tuple(ids))
                found = {int(r['id']): _row(r) for r in cur.fetchall()}
                missing = [i for i in ids if i not in found]
                if missing:
                    raise ListerError(f'proposal not found: {missing[0]}', 404)
                now = _now()
                for pid in ids:
                    cur.execute('''
                        INSERT INTO listing_helper_selections (proposal_id, upc, platform_hint, selected_by, selected_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(proposal_id) DO UPDATE SET platform_hint = excluded.platform_hint,
                            selected_by = excluded.selected_by, selected_at = excluded.selected_at
                    ''', (pid, found[pid]['upc'], platform_hint, actor, now))
                    self._event(cur, pid, found[pid]['upc'], 'helper_selected', actor, f'sent to browser lister{" for " + platform_hint if platform_hint else ""}')
            conn.commit()
            cur.execute('SELECT COUNT(*) AS n FROM listing_helper_selections')
            total = int(cur.fetchone()['n'])
        return {'selected': len(ids) if selected else 0, 'total_selected': total}

    def _event(self, cur, proposal_id, upc, event, actor, note='', payload=None):
        if not self._has_table(cur, 'listing_proposal_events'):
            return
        cur.execute('''
            INSERT INTO listing_proposal_events (proposal_id, upc, event, actor, note, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (int(proposal_id), upc, event, actor or 'lister', note or '',
              json.dumps(payload, default=str) if payload is not None else None, _now()))

    # -- links -------------------------------------------------------------------------------

    def _link_public(self, row):
        if row is None:
            return None
        row = dict(row)
        row['effects'] = _loads(row.pop('effects_json', None), {})
        return row

    def record_link(self, data, *, actor='', base_url='http://localhost/'):
        clean = validate_link(data)
        proposal_id = data.get('proposal_id') or data.get('proposalId')
        upc = _text(data.get('upc'))
        effects = {'steps': []}
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            self.init_listagent(cur)
            proposal = None
            if proposal_id not in (None, ''):
                try:
                    proposal_id = int(proposal_id)
                except (TypeError, ValueError):
                    raise ListerError('proposal_id must be an integer')
                if self._has_table(cur, 'listing_proposals'):
                    cur.execute('SELECT * FROM listing_proposals WHERE id = ?', (proposal_id,))
                    proposal = _row(cur.fetchone())
                if not proposal:
                    raise ListerError('proposal not found', 404)
                upc = upc or proposal['upc']
            upc = self.format_upc12(upc)
            if not upc:
                raise ListerError('upc is required')
            if not clean['title'] and proposal:
                clean['title'] = _text(_loads(proposal.get('proposal_json'), {}).get('title'), 200)
            if not clean['url']:
                if clean['platform'] == 'ebay':
                    clean['url'] = f"https://www.ebay.com/itm/{clean['listing_id']}"
                elif clean['asin']:
                    clean['url'] = f"https://www.amazon.com/dp/{clean['asin']}"

            # Duplicate guard: the same live listing should not be linked twice.
            key_col, key_val = ('listing_id', clean['listing_id']) if clean['platform'] == 'ebay' else ('sku', clean['sku'] or None)
            if key_val:
                cur.execute(f'SELECT * FROM listing_links WHERE platform = ? AND {key_col} = ? ORDER BY id DESC LIMIT 1', (clean['platform'], key_val))
                existing = _row(cur.fetchone())
                if existing:
                    if existing['upc'] != upc:
                        raise ListerError(f"That {clean['platform']} listing is already linked to UPC {existing['upc']}", 409)
                    return {'link': self._link_public(existing), 'duplicate': True}

            # 1. Queue: this is what Ready to Ship reads to map a store SKU / item number / ASIN to our UPC.
            cur.execute("SELECT id FROM listing_queue WHERE upc IN (%s) AND status IN ('queued', 'done') ORDER BY id DESC LIMIT 1"
                        % ','.join('?' for _ in self._variants(upc)), tuple(self._variants(upc)))
            if cur.fetchone() is None:
                cur.execute('''INSERT INTO listing_queue (upc, title, source, added_mode, status, added_at)
                               VALUES (?, ?, 'lister', 'my', 'queued', ?)''', (upc, clean['title'], _now()))
                effects['steps'].append('queue row created')
            conn.commit()
        try:
            queue_item = self.mark_listed(
                upc, platform=clean['platform'], listing_id=clean['listing_id'] or None,
                offer_id=clean['offer_id'] or None, sku=clean['sku'] or None, asin=clean['asin'] or None,
                url=clean['url'] or None, title=clean['title'] or None, price=clean['price'],
                quantity=clean['quantity'], source='lister')
            effects['queue'] = {'id': (queue_item or {}).get('id'), 'status': (queue_item or {}).get('status')}
            effects['steps'].append(f"listing queue marked listed on {clean['platform']}")
        except Exception as e:  # the queue is best effort; the link row below is the durable record
            effects['queue_error'] = str(e)[:300]

        # 2. Warehouse rows for this UPC (suffixed units included) -> store-listing fallback + Finder alias.
        rack_rows = self.rack_rows(upc)
        if rack_rows:
            match = self._write_inventory_match(clean, upc, rack_rows[0])
            effects['inventory_match'] = match
            effects['steps'].append(f"warehouse match -> {match.get('inventory_location') or 'rack'} ({match.get('inventory_barcode')})")
        else:
            effects['steps'].append('no warehouse rows with stock for this UPC; link kept on the queue only')
        effects['rack_ids'] = [r['id'] for r in rack_rows]
        if clean['store_upc'] and self.format_upc12(clean['store_upc']) != upc:
            effects['steps'].append(f"store shows UPC {clean['store_upc']}; kept our UPC {upc} as the warehouse key")

        # 3. Durable link row + proposal bookkeeping.
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            if proposal:
                effects['proposal_previous_status'] = proposal.get('status')
                if proposal.get('status') not in ('listed', 'rejected', 'superseded'):
                    cur.execute('''UPDATE listing_proposals SET status = 'listed', listed_listing_id = ?, listed_offer_id = ?,
                                   reviewed_by = ?, reviewed_at = ?, updated_at = ?, error = NULL WHERE id = ?''',
                                (clean['listing_id'] or clean['asin'] or clean['sku'], clean['offer_id'] or None,
                                 actor or 'lister', _now(), _now(), proposal['id']))
                    effects['steps'].append('proposal marked listed')
                self._event(cur, proposal['id'], upc, 'published_via_browser', actor,
                            f"{clean['platform']} {clean['listing_id'] or clean['sku'] or clean['asin']}", clean)
                cur.execute('DELETE FROM listing_helper_selections WHERE proposal_id = ?', (proposal['id'],))
            cur.execute('''
                INSERT INTO listing_links (upc, proposal_id, platform, listing_id, offer_id, sku, asin, store_upc, url,
                                           title, price, quantity, note, effects_json, created_by, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'extension', ?)
            ''', (upc, proposal['id'] if proposal else None, clean['platform'], clean['listing_id'] or None,
                  clean['offer_id'] or None, clean['sku'] or None, clean['asin'] or None, clean['store_upc'] or None,
                  clean['url'] or None, clean['title'] or None, clean['price'], clean['quantity'], clean['note'] or None,
                  json.dumps(effects, default=str), actor or None, _now()))
            link_id = cur.lastrowid
            conn.commit()
            cur.execute('SELECT * FROM listing_links WHERE id = ?', (link_id,))
            link = self._link_public(_row(cur.fetchone()))
        if self.clear_scan_cache:
            try:
                self.clear_scan_cache()
            except Exception:
                pass
        return {'link': link, 'duplicate': False}

    def rack_rows(self, upc):
        variants = self._variants(upc)
        base = variants[0].split('-', 1)[0]
        rows = []
        try:
            with self.db('searchRack.db') as conn:
                cur = conn.cursor()
                if not self._has_table(cur, 'SEARCHRACK'):
                    return rows
                placeholders = ','.join('?' for _ in variants)
                cur.execute(f'''
                    SELECT ID, BARCODE, TITLE, ITEM_POSITION, QUANTITY, IMAGE
                    FROM SEARCHRACK
                    WHERE COALESCE(QUANTITY, 0) > 0 AND (TRIM(COALESCE(BARCODE, '')) IN ({placeholders}) OR BARCODE LIKE ?)
                    ORDER BY CASE WHEN BARCODE LIKE '%-%' THEN 1 ELSE 0 END, ID ASC
                ''', (*variants, f'{base}-%'))
                for r in cur.fetchall():
                    r = _row(r)
                    rows.append({'id': int(r['ID']), 'barcode': _text(r.get('BARCODE')), 'title': _text(r.get('TITLE')),
                                 'location': _text(r.get('ITEM_POSITION')), 'quantity': r.get('QUANTITY'),
                                 'image': _text(r.get('IMAGE'))})
        except sqlite3.Error:
            return rows
        return rows

    def _write_inventory_match(self, clean, upc, rack):
        """Same rows the Store Listing Helper's manual 'Match inventory' writes, so Ready to Ship's
        store-listing fallback and the Finder learn the link."""
        store = clean['platform']
        listing_key = clean['listing_id'] if store == 'ebay' else (clean['sku'] or clean['asin'])
        listing_id = f"{store}:{listing_key}"
        from finder_aliases import update_alias
        with self.db('listing_alerts.db') as conn:
            cur = conn.cursor()
            cur.execute('''
                CREATE TABLE IF NOT EXISTS listing_inventory_matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store TEXT NOT NULL,
                    listing_key TEXT NOT NULL COLLATE NOCASE,
                    listing_id TEXT,
                    marketplace_barcode TEXT,
                    listing_title TEXT,
                    searchrack_id INTEGER NOT NULL,
                    inventory_barcode TEXT,
                    inventory_location TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(store, listing_key)
                )
            ''')
            cur.execute('''
                INSERT INTO listing_inventory_matches (store, listing_key, listing_id, marketplace_barcode, listing_title,
                                                       searchrack_id, inventory_barcode, inventory_location, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(store, listing_key) DO UPDATE SET
                    listing_id = excluded.listing_id, marketplace_barcode = excluded.marketplace_barcode,
                    listing_title = excluded.listing_title, searchrack_id = excluded.searchrack_id,
                    inventory_barcode = excluded.inventory_barcode, inventory_location = excluded.inventory_location,
                    updated_at = CURRENT_TIMESTAMP
            ''', (store, listing_key, listing_id, clean['store_upc'] or upc, clean['title'], rack['id'],
                  rack['barcode'], rack['location']))
            alias_undo = None
            try:
                alias_undo = update_alias(conn, f'{store}:{listing_key.lower()}', rack['barcode'], clean['title'] or '')
            except Exception:
                pass
            conn.commit()
        return {'store': store, 'listing_key': listing_key, 'searchrack_id': rack['id'],
                'inventory_barcode': rack['barcode'], 'inventory_location': rack['location'],
                'finder_alias_undo': alias_undo}

    def links(self, *, upc='', platform='', listing_id='', sku='', asin='', limit=100):
        clauses, params = [], []
        if upc:
            variants = self._variants(self.format_upc12(upc) or upc)
            clauses.append(f"upc IN ({','.join('?' for _ in variants)})")
            params.extend(variants)
        if platform in PLATFORMS:
            clauses.append('platform = ?')
            params.append(platform)
        if listing_id:
            clauses.append('listing_id = ?')
            params.append(_text(listing_id))
        if sku:
            clauses.append('sku = ? COLLATE NOCASE')
            params.append(_text(sku))
        if asin:
            clauses.append('asin = ? COLLATE NOCASE')
            params.append(_text(asin).upper())
        where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            cur.execute(f'SELECT * FROM listing_links {where} ORDER BY id DESC LIMIT ?', (*params, max(1, min(int(limit or 100), 500))))
            return [self._link_public(_row(r)) for r in cur.fetchall()]

    def delete_link(self, link_id, *, actor=''):
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            cur.execute('SELECT * FROM listing_links WHERE id = ?', (int(link_id),))
            link = self._link_public(_row(cur.fetchone()))
            if not link:
                raise ListerError('link not found', 404)
            effects = link.get('effects') or {}
            if link.get('proposal_id') and self._has_table(cur, 'listing_proposals'):
                previous = effects.get('proposal_previous_status')
                if previous and previous not in ('listed', 'rejected', 'superseded'):
                    cur.execute('''UPDATE listing_proposals SET status = ?, listed_listing_id = NULL, listed_offer_id = NULL,
                                   updated_at = ? WHERE id = ? AND status = 'listed' ''', (previous, _now(), link['proposal_id']))
                self._event(cur, link['proposal_id'], link['upc'], 'browser_link_removed', actor,
                            f"{link['platform']} {link.get('listing_id') or link.get('sku') or ''}")
            cur.execute('DELETE FROM listing_links WHERE id = ?', (int(link_id),))
            conn.commit()
        match = effects.get('inventory_match') or {}
        if match.get('store') and match.get('listing_key'):
            try:
                with self.db('listing_alerts.db') as conn:
                    cur = conn.cursor()
                    if self._has_table(cur, 'listing_inventory_matches'):
                        cur.execute('DELETE FROM listing_inventory_matches WHERE store = ? AND LOWER(listing_key) = LOWER(?) AND searchrack_id = ?',
                                    (match['store'], match['listing_key'], match.get('searchrack_id')))
                        conn.commit()
            except sqlite3.Error:
                pass
        if self.clear_scan_cache:
            try:
                self.clear_scan_cache()
            except Exception:
                pass
        return link

    def resolve(self, *, platform='', sku='', listing_id='', asin='', upc=''):
        """Which of our UPCs (and rack rows) does a store identifier point at?"""
        if not any([sku, listing_id, asin, upc]):
            raise ListerError('give a sku, listing_id, asin or upc')
        links = self.links(platform=platform, sku=sku, listing_id=listing_id, asin=asin, upc=upc, limit=5)
        resolved_upc = links[0]['upc'] if links else ''
        if not resolved_upc and (sku or listing_id or asin):
            with self.db('listagent.db') as conn:
                cur = conn.cursor()
                self.init_listagent(cur)
                clauses, params = [], []
                if sku:
                    clauses.append("TRIM(COALESCE(listed_sku, '')) = ? COLLATE NOCASE")
                    params.append(_text(sku))
                if listing_id:
                    clauses.append("(TRIM(COALESCE(listed_listing_id, '')) = ? OR TRIM(COALESCE(listed_offer_id, '')) = ?)")
                    params.extend([_text(listing_id), _text(listing_id)])
                if asin:
                    clauses.append("TRIM(COALESCE(listed_asin, '')) = ? COLLATE NOCASE")
                    params.append(_text(asin))
                cur.execute(f"SELECT upc FROM listing_queue WHERE {' OR '.join(clauses)} ORDER BY COALESCE(listed_at, added_at) DESC, id DESC LIMIT 1", tuple(params))
                row = cur.fetchone()
                resolved_upc = row['upc'] if row else ''
        if not resolved_upc and upc:
            resolved_upc = self.format_upc12(upc)
        return {'upc': resolved_upc, 'links': links, 'rack': self.rack_rows(resolved_upc) if resolved_upc else []}

    # -- extension update feed -------------------------------------------------------------

    def feed_file(self, filename):
        if not self.feed_dir.is_dir():
            return None
        return send_from_directory(str(self.feed_dir), filename)


FALLBACK_PAGE = '''<!doctype html><meta charset="utf-8"><title>Sweet Shelves Lister</title>
<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:48px auto;padding:0 24px;line-height:1.6;color:#1f2937}code{background:#f3f4f6;padding:2px 6px;border-radius:4px}</style>
<h1>Sweet Shelves Lister</h1>
<p>No extension release has been published to this server yet.</p>
<p>From the development PC run <code>powershell -File tools/publish-lister.ps1</code>. It packages
<code>lister-extension/</code>, uploads the checksummed release to <code>static/lister/</code> and this page becomes the update page.</p>
'''


def register(app, deps):
    """Wire the Lister routes. deps is a mapping (module globals or a dict) that provides
    db_connection, _safe_error, _listagent_mark_listed, _listagent_upc_variants, _listagent_format_upc12,
    _listagent_init_tables and optionally _listing_helper_scan_cache_clear."""
    lister = Lister(deps, app.static_folder)

    def base_url():
        try:
            return (request.url_root or '').strip() or 'http://localhost/'
        except Exception:
            return 'http://localhost/'

    def actor():
        data = request.get_json(silent=True) if request.method in ('POST', 'DELETE') else None
        value = _text((data or {}).get('actor') or (data or {}).get('reviewed_by') or request.args.get('actor'), 80)
        return value or _text(request.headers.get('Cf-Access-Authenticated-User-Email'), 120) or 'lister'

    def guard_mutation():
        if request.headers.get('Sec-Fetch-Site') == 'cross-site' and not request.headers.get(MUTATION_HEADER):
            raise ListerError('Use the Sweet Shelves Lister extension or a Sweet Shelves page for this action.', 403)

    def failure(e, context):
        if isinstance(e, ListerError):
            return jsonify({'success': False, 'error': str(e)}), e.status
        return jsonify({'success': False, 'error': lister.safe_error(e, context)}), 500

    def lister_feed_index():
        response = lister.feed_file('index.html') if (lister.feed_dir / 'index.html').is_file() else None
        if response is None:
            return FALLBACK_PAGE, 200, {'Content-Type': 'text/html; charset=utf-8'}
        return response

    def lister_feed_file(filename):
        response = lister.feed_file(filename)
        if response is None:
            return jsonify({'success': False, 'error': 'No release published'}), 404
        if filename.startswith('releases/'):
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        else:
            response.headers['Cache-Control'] = 'no-store'
        return response

    def api_lister_ping():
        return jsonify({'success': True, 'version': VERSION, 'server_time': _now(),
                        'user': _text(request.headers.get('Cf-Access-Authenticated-User-Email'), 120),
                        'mutation_header': MUTATION_HEADER})

    def api_lister_items():
        try:
            items, counts = lister.items(scope=request.args.get('scope') or 'selected',
                                         platform=_text(request.args.get('platform')).lower(), base_url=base_url())
            return jsonify({'success': True, 'items': items, 'counts': counts, 'version': VERSION})
        except Exception as e:
            return failure(e, 'lister:items')

    def api_lister_select():
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            ids = data.get('proposal_ids')
            if ids is None and data.get('proposal_id') is not None:
                ids = [data.get('proposal_id')]
            result = lister.select(ids, selected=bool(data.get('selected', True)), actor=actor(),
                                   platform_hint=_text(data.get('platform')).lower())
            return jsonify({'success': True, **result})
        except Exception as e:
            return failure(e, 'lister:select')

    def api_lister_links_get():
        try:
            links = lister.links(upc=_text(request.args.get('upc')), platform=_text(request.args.get('platform')).lower(),
                                 listing_id=_text(request.args.get('listing_id')), sku=_text(request.args.get('sku')),
                                 asin=_text(request.args.get('asin')), limit=request.args.get('limit') or 100)
            return jsonify({'success': True, 'links': links})
        except Exception as e:
            return failure(e, 'lister:links')

    def api_lister_links_post():
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            result = lister.record_link(data, actor=actor(), base_url=base_url())
            return jsonify({'success': True, **result}), (200 if result.get('duplicate') else 201)
        except Exception as e:
            return failure(e, 'lister:link')

    def api_lister_link_delete(link_id):
        try:
            guard_mutation()
            link = lister.delete_link(link_id, actor=actor())
            return jsonify({'success': True, 'removed': link})
        except Exception as e:
            return failure(e, 'lister:unlink')

    def api_lister_resolve():
        try:
            result = lister.resolve(platform=_text(request.args.get('platform')).lower(), sku=_text(request.args.get('sku')),
                                    listing_id=_text(request.args.get('listing_id')), asin=_text(request.args.get('asin')),
                                    upc=_text(request.args.get('upc')))
            return jsonify({'success': True, **result})
        except Exception as e:
            return failure(e, 'lister:resolve')

    def api_lister_events():
        """The panel reports fills so the proposal timeline shows what reached the store page."""
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            try:
                proposal_id = int(data.get('proposal_id'))
            except (TypeError, ValueError):
                raise ListerError('proposal_id is required')
            event = _text(data.get('event'), 40)
            if event not in ('helper_filled', 'helper_opened', 'helper_fill_failed'):
                raise ListerError('unknown event')
            with lister.db('listagent.db') as conn:
                cur = conn.cursor()
                lister.init_tables(cur)
                if not lister._has_table(cur, 'listing_proposals'):
                    raise ListerError('proposal not found', 404)
                cur.execute('SELECT upc FROM listing_proposals WHERE id = ?', (proposal_id,))
                row = cur.fetchone()
                if not row:
                    raise ListerError('proposal not found', 404)
                payload = data.get('payload') if isinstance(data.get('payload'), dict) else None
                lister._event(cur, proposal_id, row['upc'], event, actor(), _text(data.get('note'), 300), payload)
                conn.commit()
            return jsonify({'success': True})
        except Exception as e:
            return failure(e, 'lister:event')

    app.add_url_rule('/lister/', 'lister_feed_index', lister_feed_index)
    app.add_url_rule('/lister/<path:filename>', 'lister_feed_file', lister_feed_file)
    app.add_url_rule('/api/lister/ping', 'api_lister_ping', api_lister_ping)
    app.add_url_rule('/api/lister/items', 'api_lister_items', api_lister_items)
    app.add_url_rule('/api/lister/select', 'api_lister_select', api_lister_select, methods=['POST'])
    app.add_url_rule('/api/lister/links', 'api_lister_links_get', api_lister_links_get)
    app.add_url_rule('/api/lister/links', 'api_lister_links_post', api_lister_links_post, methods=['POST'])
    app.add_url_rule('/api/lister/links/<int:link_id>', 'api_lister_link_delete', api_lister_link_delete, methods=['DELETE'])
    app.add_url_rule('/api/lister/resolve', 'api_lister_resolve', api_lister_resolve)
    app.add_url_rule('/api/lister/events', 'api_lister_events', api_lister_events, methods=['POST'])
    return lister
