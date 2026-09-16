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

import base64
import datetime
import html as html_module
import json
import mimetypes
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from flask import jsonify, render_template, request, send_from_directory

VERSION = '0.2.0'
PLATFORMS = ('ebay', 'amazon')
OPEN_STATUSES = ('proposed', 'held', 'needs_photos', 'blocked')
MUTATION_HEADER = 'X-Sweet-Shelves-Lister'
FEED_REQUIRED = ('manifest.json', 'background.js', 'sidepanel.html')

# Prep notes that mean the unit is not new (same list the Listing Agent gate uses).
DAMAGE_WORDS = (
    'damage', 'damaged', 'scratch', 'scratched', 'dent', 'dented', 'broken', 'crack', 'cracked', 'torn',
    'tear', 'missing', 'stain', 'stained', 'chip', 'chipped', 'worn', 'defect', 'open box', 'opened',
)

# "AI photoshop": one OpenAI image edit per photo. The prompt keeps the product untouched.
OPENAI_IMAGE_MODEL = 'gpt-image-1'
DEFAULT_AI_PHOTO_PROMPT = (
    'Clean up this product photo for an online marketplace listing. Replace the background with a plain, '
    'evenly lit white studio background and fix the exposure and white balance. Keep the product itself exactly '
    'as it is: same shape, colors, labels, printed text, wear and any damage. Do not add, remove or retouch '
    'anything on the product. Keep the whole product visible and centered.'
)
AI_PHOTO_MAX_BYTES = 20_000_000
AI_PHOTO_TIMEOUT = 100  # gunicorn gives a request 120 s

# A proposal build (eBay catalog, comps, category, Claude title/description) can take a minute.
PREPARE_STALE_SECONDS = 600

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


def _base_upc(upc):
    """'035886267162-1' -> '035886267162': the catalog code a store search understands."""
    return _text(upc).split('-', 1)[0].strip()


def is_suffixed(upc):
    return '-' in _text(upc)


def condition_from_notes(notes, *, suffixed=False, default='NEW_OTHER'):
    """Condition + condition note from prep evidence. A flaw word means used; the note text becomes
    the store's condition description. English voice-note text is expected to be in `notes` already."""
    cleaned = []
    for note in notes or []:
        text = _text(note)
        if text and text not in cleaned:
            cleaned.append(text)
    joined = '\n'.join(cleaned)
    lowered = joined.lower()
    damaged = any(word in lowered for word in DAMAGE_WORDS)
    if damaged:
        return {'condition': 'USED_GOOD', 'conditionDescription': joined[:1000],
                'reason': 'prep notes mention a flaw', 'assumed': False}
    if suffixed:
        return {'condition': default, 'conditionDescription': joined[:1000],
                'reason': 'suffixed unit without a flaw note; confirm the condition', 'assumed': True}
    return {'condition': default, 'conditionDescription': joined[:1000],
            'reason': 'no flaw recorded; default condition assumed', 'assumed': True}


def note_text_variants(note):
    """Item Prep stores 'LT: ... | EN: ...'. The English half is what a listing should use."""
    text = _text(note)
    if not text:
        return ''
    match = re.search(r'(?:^|\|)\s*EN:\s*(.+?)\s*$', text, flags=re.S)
    if match:
        return _text(match.group(1))
    if text.upper().startswith('LT:') and '|' not in text:
        return ''  # Lithuanian only; the caller may translate it
    return text


def qr_svg(text):
    """A QR code as an SVG document, or None when the qrcode package is missing."""
    try:
        import qrcode
        import qrcode.image.svg
    except Exception:
        return None
    image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    return image.to_string(encoding='unicode') if hasattr(image, 'to_string') else None


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
        # Stores only know the catalog UPC; the -suffix stays on the SKU so the sale traces to that unit.
        'upc': _base_upc(upc),
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
    def __init__(self, deps, static_folder, app=None):
        self.app = app
        self.db = deps['db_connection']
        self.safe_error = deps['_safe_error']
        self.mark_listed = deps['_listagent_mark_listed']
        self.upc_variants = deps['_listagent_upc_variants']
        self.format_upc12 = deps['_listagent_format_upc12']
        self.init_listagent = deps['_listagent_init_tables']
        self.clear_scan_cache = deps.get('_listing_helper_scan_cache_clear')
        # Queue-driven lister (0.2): everything below is optional so older hosts keep the 0.1 routes.
        self.upc_detail_view = deps.get('api_listingagent_upc_detail')
        self.build_proposal = deps.get('_agent_build_proposal')
        self.remove_from_queue = deps.get('_listagent_remove_from_queue')
        self.mark_bol_listed = deps.get('_listing_center_mark_bol_listed')
        self.add_listing_photo = deps.get('_listagent_add_photo')
        self.bump_data_version = deps.get('update_data_version')
        self.base_dir = deps.get('BASE_DIR')
        self.static_folder = Path(static_folder)
        self.feed_dir = self.static_folder / 'lister'
        self._jobs = {}
        self._jobs_lock = threading.Lock()

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
        # Per-store "X" on the side panel: the item leaves that store's list but stays queued for the other.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS lister_queue_state (
                upc TEXT NOT NULL,
                platform TEXT NOT NULL,
                state TEXT NOT NULL,
                actor TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (upc, platform)
            )
        ''')

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
        # /items-to-list Marketplaces box for this UPC (source shows as "Listing Agent").
        if self.mark_bol_listed:
            try:
                marked = self.mark_bol_listed(clean['platform'], upc=upc)
                if isinstance(marked, dict) and marked.get('success') is False:
                    effects['bol_error'] = _text(marked.get('error'), 200)
                else:
                    effects['steps'].append(f"Items to List marked listed on {clean['platform']}")
            except Exception as e:
                effects['bol_error'] = str(e)[:300]
        try:
            finalized = self.finalize_queue(upc)
            if finalized:
                effects['steps'].append(f"queue item {finalized}")
        except Exception as e:
            effects['queue_error'] = (effects.get('queue_error') or '') + ' finalize: ' + str(e)[:200]

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

    # -- queue-driven lister (0.2) --------------------------------------------------------------

    def _queue_rows(self, cur):
        """Active Listing Agent queue rows, oldest first (the first one is what the panel works on)."""
        self.init_listagent(cur)
        self.init_tables(cur)
        cur.execute('''
            SELECT * FROM listing_queue WHERE status IN ('queued', 'done')
            ORDER BY added_at ASC, id ASC LIMIT 400
        ''')
        rows = [_row(r) for r in cur.fetchall()]
        cur.execute("SELECT upc, platform, created_at FROM lister_queue_state WHERE state = 'skipped'")
        skips = {(r['upc'], r['platform']): r['created_at'] for r in cur.fetchall()}
        return rows, skips

    def _skip_key(self, upc, platform):
        return (self.format_upc12(upc) or _text(upc), platform)

    def _thumbs(self, upcs):
        """Catalog thumbnail per UPC from the BOL tables (one query per database, not per item)."""
        out = {}
        bases = {}
        for upc in upcs:
            base = _base_upc(upc)
            for key in {base, base.lstrip('0') or base, base.zfill(12) if base.isdigit() else base}:
                if key:
                    bases.setdefault(key, set()).add(upc)
        if not bases:
            return out
        keys = tuple(bases)
        placeholders = ','.join('?' for _ in keys)
        for db_name, query in (
            ('rawbol.db', f"SELECT upc, image_url FROM raw_bol_items WHERE TRIM(COALESCE(upc, '')) IN ({placeholders}) AND COALESCE(image_url, '') != '' ORDER BY rowid DESC"),
            ('bol.db', f"SELECT upc, image_url FROM bol_items WHERE TRIM(COALESCE(upc, '')) IN ({placeholders}) AND COALESCE(image_url, '') != '' ORDER BY id DESC"),
        ):
            try:
                with self.db(db_name) as conn:
                    cur = conn.cursor()
                    cur.execute(query, keys)
                    for r in cur.fetchall():
                        r = _row(r)
                        image = _text(r.get('image_url'))
                        if image.lower() in ('nan', 'none', 'null'):
                            continue
                        for upc in bases.get(_text(r.get('upc')), ()):
                            out.setdefault(upc, image)
            except sqlite3.Error:
                continue
        return out

    def _latest_proposals(self, cur, upcs):
        """Newest non-superseded proposal per UPC: id, status and whether it carries listing fields."""
        out = {}
        if not upcs or not self._has_table(cur, 'listing_proposals'):
            return out
        placeholders = ','.join('?' for _ in upcs)
        cur.execute(f'''SELECT id, upc, status, proposal_json, flags_json, eligibility_json, updated_at FROM listing_proposals
                        WHERE upc IN ({placeholders}) AND status != 'superseded' ORDER BY id DESC''', tuple(upcs))
        for r in cur.fetchall():
            r = _row(r)
            if r['upc'] in out:
                continue
            data = _loads(r.get('proposal_json'), {})
            out[r['upc']] = {'id': int(r['id']), 'status': r.get('status'), 'ready': bool(_text(data.get('title'))),
                             'updatedAt': r.get('updated_at') or '', 'proposal': data,
                             'flags': [f for f in _loads(r.get('flags_json'), []) if isinstance(f, dict)],
                             'eligibility': _loads(r.get('eligibility_json'), {})}
        return out

    def _job_state(self, upc):
        with self._jobs_lock:
            job = self._jobs.get(upc)
        if not job:
            return {}
        if job.get('running') and time.time() - job.get('started', 0) > PREPARE_STALE_SECONDS:
            job = {**job, 'running': False, 'error': 'timed out'}
        return {'running': bool(job.get('running')), 'error': job.get('error') or '', 'startedAt': job.get('started_iso') or ''}

    @staticmethod
    def store_url(platform, entry):
        if platform == 'ebay' and entry.get('listingId'):
            return f"https://www.ebay.com/itm/{entry['listingId']}"
        if platform == 'amazon' and entry.get('asin'):
            return f"https://www.amazon.com/dp/{entry['asin']}"
        return ''

    def queue(self, *, platform, base_url='http://localhost/'):
        """The side panel's list for one store: queued items (oldest first) then the ones listed there."""
        if platform not in PLATFORMS:
            raise ListerError('platform must be ebay or amazon')
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            rows, skips = self._queue_rows(cur)
            upcs = [self.format_upc12(r['upc']) or _text(r['upc']) for r in rows]
            links = self._links_for_upcs(cur, list({*upcs, *[r['upc'] for r in rows]}))
            proposals = self._latest_proposals(cur, upcs)
        thumbs = self._thumbs(upcs)
        active, done, hidden = [], [], 0
        other = 'amazon' if platform == 'ebay' else 'ebay'
        for row, upc in zip(rows, upcs):
            listed_at = row.get(f'listed_{platform}_at') or ''
            skipped_at = skips.get((upc, platform)) or skips.get((row['upc'], platform)) or ''
            if skipped_at and not listed_at:
                hidden += 1
                continue
            stores = self._store_listings(upc)
            existing = stores.get(platform) or []
            live = [e for e in existing if not e.get('state') or e['state'].lower() in ('active', 'live')]
            own_links = [l for l in links.get(upc, []) + links.get(row['upc'], []) if l['platform'] == platform]
            proposal = proposals.get(upc) or {}
            item = {
                'id': int(row['id']),
                'upc': upc,
                'baseUpc': _base_upc(upc),
                'suffixed': is_suffixed(upc),
                'title': _text(row.get('title'), 200),
                'thumb': thumbs.get(upc, ''),
                'addedAt': row.get('added_at') or '',
                'source': row.get('source') or '',
                'platform': platform,
                'status': 'listed' if listed_at else 'queued',
                'listedAt': listed_at,
                'listed': {'ebay': bool(row.get('listed_ebay_at')), 'amazon': bool(row.get('listed_amazon_at'))},
                'skipped': {'ebay': bool(skips.get((upc, 'ebay'))), 'amazon': bool(skips.get((upc, 'amazon')))},
                'otherStatus': 'listed' if row.get(f'listed_{other}_at') else ('skipped' if skips.get((upc, other)) else 'queued'),
                'existing': existing,
                'storeUrl': (self.store_url(platform, live[0]) if live else '') or (own_links[0].get('url') if own_links else ''),
                'alreadyOnStore': bool(live) and not listed_at,
                'links': own_links,
                'proposal': {k: proposal[k] for k in ('id', 'status', 'ready', 'updatedAt') if k in proposal},
                'preparing': self._job_state(upc).get('running', False),
                'checkRequest': bool(row.get('has_check_request')),
            }
            (done if listed_at else active).append(item)
        done.sort(key=lambda it: -_order_key(it['listedAt']))
        return {'items': active + done, 'counts': {'queued': len(active), 'listed': len(done), 'hidden': hidden}}

    def _upc_detail(self, upc, base_url):
        """The Listing Agent's aggregate view of one UPC (inventory, BOL, stores, prep notes/photos/voice)."""
        if not self.upc_detail_view or not self.app:
            return {}
        path = f'/api/listingagent/upc/{upc}'
        with self.app.test_request_context(path, base_url=base_url):
            rv = self.upc_detail_view(upc)
        if isinstance(rv, tuple):
            rv = rv[0]
        data = rv.get_json(silent=True) if hasattr(rv, 'get_json') else (rv or {})
        return (data or {}).get('item') or {} if (data or {}).get('success') else {}

    def _voice_analysis(self, media_ids):
        """English/Lithuanian text already produced for prep voice notes (voice_note_analysis in bol.db)."""
        out = {}
        ids = [int(i) for i in media_ids if str(i).isdigit()]
        if not ids:
            return out
        try:
            with self.db('bol.db') as conn:
                cur = conn.cursor()
                if not self._has_table(cur, 'voice_note_analysis'):
                    return out
                cur.execute(f"SELECT * FROM voice_note_analysis WHERE media_id IN ({','.join('?' for _ in ids)})", tuple(ids))
                for r in cur.fetchall():
                    r = _row(r)
                    running = float(r.get('lease_until') or 0) > time.time()
                    out[int(r['media_id'])] = {
                        'lithuanian': r.get('lithuanian') or '', 'english': r.get('english') or '',
                        'status': 'processing' if running else ('complete' if r.get('english') else ('error' if r.get('error') else 'pending')),
                        'error': r.get('error') or '',
                    }
        except sqlite3.Error:
            pass
        return out

    def detail(self, upc, *, base_url='http://localhost/'):
        """Everything the panel needs to list one unit: fields to fill, notes, voice notes, photos, stock."""
        upc = self.format_upc12(upc)
        if not upc:
            raise ListerError('upc is required')
        detail = self._upc_detail(upc, base_url)
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_listagent(cur)
            self.init_tables(cur)
            proposal = (self._latest_proposals(cur, [upc]) or {}).get(upc) or {}
            links = self._links_for_upcs(cur, [upc]).get(upc, [])
            cur.execute("SELECT * FROM listing_queue WHERE upc IN (%s) AND status IN ('queued', 'done') ORDER BY id DESC LIMIT 1"
                        % ','.join('?' for _ in self._variants(upc)), tuple(self._variants(upc)))
            queue_row = _row(cur.fetchone()) or {}
            cur.execute("SELECT platform FROM lister_queue_state WHERE upc = ? AND state = 'skipped'", (upc,))
            skipped = [r['platform'] for r in cur.fetchall()]
            own_photos = []
            if self._has_table(cur, 'listing_photos'):
                cur.execute(f"SELECT id, image_path, created_at, original_filename FROM listing_photos WHERE upc IN ({','.join('?' for _ in self._variants(upc))}) ORDER BY id DESC LIMIT 60",
                            tuple(self._variants(upc)))
                own_photos = [_row(r) for r in cur.fetchall()]
        prep = detail.get('prep') or {}
        voice_rows = prep.get('voice_notes') or []
        analysis = self._voice_analysis([v.get('id') for v in voice_rows])
        voice_notes = []
        english_notes = []
        for v in voice_rows:
            a = analysis.get(int(v['id'])) if str(v.get('id', '')).isdigit() else None
            entry = {'id': v.get('id'), 'url': v.get('url'), 'createdAt': v.get('created_at') or '',
                     'english': (a or {}).get('english', ''), 'lithuanian': (a or {}).get('lithuanian', ''),
                     'status': (a or {}).get('status', 'pending'), 'error': (a or {}).get('error', '')}
            voice_notes.append(entry)
            if entry['english']:
                english_notes.append(entry['english'])
        written_notes = []
        for n in prep.get('notes') or []:
            text = n.get('note') if isinstance(n, dict) else n
            written_notes.append({'id': (n.get('id') if isinstance(n, dict) else None), 'text': _text(text),
                                  'english': note_text_variants(text), 'createdAt': (n.get('created_at') if isinstance(n, dict) else '') or ''})
        defect = _text(detail.get('defect'))
        note_texts = ([defect] if defect else []) + [n['english'] or n['text'] for n in written_notes if (n['english'] or n['text'])] + english_notes
        condition = condition_from_notes(note_texts, suffixed=is_suffixed(upc))

        photos = []
        seen = set()

        def add_photo(url, source, photo_id=None):
            url = _absolute(url, base_url)
            if not url or url in seen:
                return
            seen.add(url)
            photos.append({'url': url, 'source': source, 'id': photo_id, 'name': url.rsplit('/', 1)[-1].split('?')[0]})

        for p in own_photos:
            rel = _text(p.get('image_path'))
            if rel:
                add_photo(f'/static/{rel}', 'ai' if '_ai_' in rel else 'listing', p.get('id'))
        for url in prep.get('images') or []:
            add_photo(url, 'prep')
        for url in detail.get('images') or []:
            add_photo(url, 'catalog')

        inventory = detail.get('inventory') or {}
        total_qty = int(inventory.get('total_quantity') or 0)
        proposal_data = proposal.get('proposal') or {}
        eligibility = proposal.get('eligibility') or {}
        if proposal.get('ready'):
            fields = fill_fields(proposal_data, eligibility, upc=upc, base_url=base_url)
            fields['source'] = 'proposal'
        else:
            fields = fill_fields({}, {}, upc=upc, base_url=base_url)
            fields['title'] = _text(detail.get('title') or queue_row.get('title'), 80)
            fields['quantity'] = 1 if is_suffixed(upc) else (total_qty or None)
            fields['source'] = 'inventory'
        if is_suffixed(upc):
            fields['quantity'] = 1
        if not fields.get('condition') or (condition['condition'] == 'USED_GOOD' and not condition['assumed']):
            fields['condition'] = condition['condition']
        fields['amazonCondition'] = AMAZON_CONDITIONS.get(fields['condition'], '')
        if not fields.get('conditionDescription') and condition['conditionDescription']:
            fields['conditionDescription'] = condition['conditionDescription']
        if not fields.get('images'):
            fields['images'] = [p['url'] for p in photos if p['source'] != 'catalog'][:12] or [p['url'] for p in photos][:12]
        if not fields.get('descriptionText'):
            parts = [fields['title']]
            if fields.get('conditionDescription'):
                parts.append('Condition: ' + fields['conditionDescription'])
            fields['descriptionText'] = '\n\n'.join(p for p in parts if p)
        bol = detail.get('bol') or {}
        cost = None
        for key in ('avg_cost', 'cost', 'unit_cost'):
            if bol.get(key) not in (None, ''):
                try:
                    cost = round(float(bol[key]), 2)
                    break
                except (TypeError, ValueError):
                    pass
        return {
            'upc': upc,
            'baseUpc': _base_upc(upc),
            'suffixed': is_suffixed(upc),
            'title': fields['title'],
            'fields': fields,
            'condition': condition,
            'notes': written_notes,
            'defect': defect,
            'voiceNotes': voice_notes,
            'videos': prep.get('videos') or [],
            'photos': photos,
            'inventory': {'quantity': total_qty, 'positions': inventory.get('positions') or [],
                          'rows': (inventory.get('rows') or [])[:10]},
            'cost': cost,
            'bol': {k: bol.get(k) for k in ('description', 'lot_number', 'original_retail', 'avg_cost') if isinstance(bol, dict) and k in bol},
            'existing': {'ebay': (detail.get('ebay_store') or {}).get('listings') or [],
                         'amazon': (detail.get('amazon_store') or {}).get('listings') or []},
            'links': links,
            'queue': {'id': queue_row.get('id'), 'status': queue_row.get('status'),
                      'listed': {'ebay': bool(queue_row.get('listed_ebay_at')), 'amazon': bool(queue_row.get('listed_amazon_at'))},
                      'skipped': skipped},
            'proposal': {k: proposal[k] for k in ('id', 'status', 'ready', 'updatedAt', 'flags') if k in proposal},
            'preparing': self._job_state(upc),
            'mobilePhotosUrl': base_url.rstrip('/') + '/items-to-list/mobile-photos?upc=' + upc + '&return=%2Fitems-to-list',
            'aiPhotoPrompt': DEFAULT_AI_PHOTO_PROMPT,
        }

    def prepare(self, upc, *, base_url='http://localhost/', force=False, actor='lister'):
        """Build the Listing Agent proposal (title, price, category, specifics, description) in the background."""
        upc = self.format_upc12(upc)
        if not upc:
            raise ListerError('upc is required')
        if not self.build_proposal or not self.app:
            raise ListerError('The proposal builder is not available on this server.', 501)
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_listagent(cur)
            proposal = (self._latest_proposals(cur, [upc]) or {}).get(upc) or {}
        if proposal.get('ready') and not force:
            return {'status': 'ready', 'proposalId': proposal['id']}
        with self._jobs_lock:
            job = self._jobs.get(upc)
            if job and job.get('running') and time.time() - job.get('started', 0) <= PREPARE_STALE_SECONDS:
                return {'status': 'preparing'}
            self._jobs[upc] = {'running': True, 'started': time.time(), 'started_iso': _now(), 'error': ''}
        app, build = self.app, self.build_proposal

        def run():
            error = ''
            try:
                with app.app_context():
                    build(upc, actor=actor, base_url=base_url)
            except Exception as e:  # reported through the detail endpoint, never raised in the thread
                error = str(e)[:300] or type(e).__name__
            with self._jobs_lock:
                self._jobs[upc] = {'running': False, 'started': time.time(), 'started_iso': _now(), 'error': error}

        threading.Thread(target=run, name=f'lister-prepare-{upc}', daemon=True).start()
        return {'status': 'preparing'}

    def skip(self, upc, *, platform, actor=''):
        """X on the panel: hide the item from this store's list. Gone from both lists means out of the queue."""
        upc = self.format_upc12(upc)
        if not upc:
            raise ListerError('upc is required')
        if platform not in PLATFORMS:
            raise ListerError('platform must be ebay or amazon')
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            cur.execute('''INSERT INTO lister_queue_state (upc, platform, state, actor, created_at) VALUES (?, ?, 'skipped', ?, ?)
                           ON CONFLICT(upc, platform) DO UPDATE SET state = 'skipped', actor = excluded.actor, created_at = excluded.created_at''',
                        (upc, platform, actor or None, _now()))
            conn.commit()
        return {'upc': upc, 'platform': platform, 'queue': self.finalize_queue(upc) or 'queued'}

    def unskip(self, upc, *, platform):
        upc = self.format_upc12(upc)
        if platform not in PLATFORMS:
            raise ListerError('platform must be ebay or amazon')
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            self.init_listagent(cur)
            cur.execute('DELETE FROM lister_queue_state WHERE upc = ? AND platform = ?', (upc, platform))
            variants = self._variants(upc)
            cur.execute(f"SELECT id, status FROM listing_queue WHERE upc IN ({','.join('?' for _ in variants)}) ORDER BY id DESC LIMIT 1", tuple(variants))
            row = _row(cur.fetchone())
            if row and row['status'] in ('removed', 'done'):
                cur.execute("UPDATE listing_queue SET status = 'queued', removed_at = NULL WHERE id = ?", (row['id'],))
            conn.commit()
        return {'upc': upc, 'platform': platform, 'queue': 'queued'}

    def finalize_queue(self, upc):
        """Once both stores are listed or skipped, the queue row is done (something listed) or removed."""
        upc = self.format_upc12(upc)
        with self.db('listagent.db') as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            self.init_listagent(cur)
            variants = self._variants(upc)
            cur.execute(f"SELECT * FROM listing_queue WHERE upc IN ({','.join('?' for _ in variants)}) AND status IN ('queued', 'done') ORDER BY id DESC LIMIT 1", tuple(variants))
            row = _row(cur.fetchone())
            if not row:
                return ''
            cur.execute("SELECT platform FROM lister_queue_state WHERE upc = ? AND state = 'skipped'", (upc,))
            skipped = {r['platform'] for r in cur.fetchall()}
            resolved = all(row.get(f'listed_{p}_at') or p in skipped for p in PLATFORMS)
            if not resolved:
                return ''
            listed_any = any(row.get(f'listed_{p}_at') for p in PLATFORMS)
            if listed_any:
                cur.execute("UPDATE listing_queue SET status = 'done' WHERE id = ?", (row['id'],))
                conn.commit()
                return 'done'
            cur.execute("UPDATE listing_queue SET status = 'removed', removed_at = ? WHERE id = ?", (_now(), row['id']))
            conn.commit()
            return 'removed'

    def mark_existing(self, upc, *, platform, entry, actor='', base_url='http://localhost/'):
        """The store already carries this UPC: record that listing as the link without touching the page."""
        entry = entry or {}
        body = {'platform': platform, 'upc': upc, 'listing_id': entry.get('listingId') or '', 'sku': entry.get('sku') or '',
                'asin': entry.get('asin') or '', 'store_upc': entry.get('upc') or '', 'title': entry.get('title') or '',
                'note': 'already on the store; linked from the side panel'}
        return self.record_link(body, actor=actor, base_url=base_url)

    # -- voice notes ---------------------------------------------------------------------------

    def analyze_voice(self, media_id, *, reanalyze=False):
        """Transcribe (Whisper) and translate (Claude) one prep voice note through the shared service."""
        from voice_note_routes import VoiceNotes, VoiceError
        base_dir = self.base_dir or self.static_folder.parent
        service = VoiceNotes(base_dir, str(self.static_folder))
        try:
            return service.analyze(int(media_id), reanalyze=bool(reanalyze))
        except VoiceError as e:
            raise ListerError(str(e), getattr(e, 'status', 502))

    # -- photos ---------------------------------------------------------------------------------

    def local_photo(self, url, base_url):
        """Absolute path of one of our own static images, or None for anything outside static/."""
        value = _text(url)
        if not value:
            return None
        for prefix in (base_url.rstrip('/'), ''):
            candidate = value[len(prefix):] if prefix and value.startswith(prefix) else (value if not prefix else '')
            if candidate.startswith('/static/'):
                rel = candidate[len('/static/'):].split('?', 1)[0]
                path = (self.static_folder / rel).resolve()
                try:
                    inside = path.is_relative_to(self.static_folder.resolve())
                except AttributeError:
                    inside = str(path).startswith(str(self.static_folder.resolve()))
                if inside and path.is_file():
                    return path
        return None

    def photo_bytes(self, url, base_url):
        """(bytes, mime, name) for a photo: our static file directly, anything else fetched over HTTPS."""
        path = self.local_photo(url, base_url)
        if path is not None:
            mime = mimetypes.guess_type(path.name)[0] or 'image/jpeg'
            data = path.read_bytes()
            if len(data) > AI_PHOTO_MAX_BYTES:
                raise ListerError('That photo is larger than 20 MB.', 413)
            return data, mime, path.name
        value = _text(url)
        if value.startswith(base_url.rstrip('/') + '/') or value.startswith('/'):
            raise ListerError('That photo is not one of our static files.')
        if not value.lower().startswith('https://'):
            raise ListerError('Photos must be Sweet Shelves static files or https images.')
        import requests
        try:
            response = requests.get(value, timeout=20, stream=True)
        except requests.RequestException:
            raise ListerError('Could not download that photo.', 502)
        if response.status_code != 200:
            raise ListerError(f'Could not download that photo (HTTP {response.status_code}).', 502)
        data = response.raw.read(AI_PHOTO_MAX_BYTES + 1)
        if len(data) > AI_PHOTO_MAX_BYTES:
            raise ListerError('That photo is larger than 20 MB.', 413)
        mime = (response.headers.get('Content-Type') or 'image/jpeg').split(';')[0].strip()
        name = value.rsplit('/', 1)[-1].split('?')[0] or 'photo.jpg'
        return data, mime, name

    def ai_photo(self, upc, *, url, prompt='', base_url='http://localhost/', quality='medium'):
        """Send one photo through the OpenAI image edit and store the result as a listing photo."""
        upc = self.format_upc12(upc)
        if not upc:
            raise ListerError('upc is required')
        api_key = os.getenv('OPENAI_API_KEY', '').strip()
        if not api_key:
            raise ListerError('AI photoshop needs server setup: add OPENAI_API_KEY to the Pi .env and restart the service.', 503)
        prompt = _text(prompt, 2000) or DEFAULT_AI_PHOTO_PROMPT
        quality = quality if quality in ('low', 'medium', 'high', 'auto') else 'medium'
        data, mime, name = self.photo_bytes(url, base_url)
        import requests
        try:
            response = requests.post(
                'https://api.openai.com/v1/images/edits',
                headers={'Authorization': f'Bearer {api_key}'},
                files=[('image[]', (name, data, mime))],
                data={'model': OPENAI_IMAGE_MODEL, 'prompt': prompt, 'n': '1', 'quality': quality, 'size': 'auto',
                      'output_format': 'jpeg'},
                timeout=AI_PHOTO_TIMEOUT,
            )
        except requests.RequestException:
            raise ListerError('The image service could not be reached. Try again in a moment.', 504)
        if response.status_code != 200:
            try:
                message = ((response.json().get('error') or {}).get('message') or '')[:300]
            except Exception:
                message = response.text[:300]
            raise ListerError(f'Image edit failed (HTTP {response.status_code}). {message}'.strip(), 502)
        try:
            result = response.json()
            image_b64 = result['data'][0]['b64_json']
            output = base64.b64decode(image_b64)
        except Exception:
            raise ListerError('The image service returned no picture.', 502)
        try:
            from ai_usage import record as record_ai_usage
            record_ai_usage('openai', OPENAI_IMAGE_MODEL, 'Lister AI photoshop', result)
        except Exception:
            pass
        folder = self.static_folder / 'listingagent_uploads'
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{upc}_ai_{int(time.time() * 1000)}.jpg"
        (folder / filename).write_bytes(output)
        rel = f'listingagent_uploads/{filename}'
        photo_id = None
        if self.add_listing_photo:
            try:
                saved = self.add_listing_photo(upc, image_path=rel, original_filename=f'ai:{name}', size_bytes=len(output))
                photo_id = (saved or {}).get('id')
            except Exception:
                photo_id = None
        if self.bump_data_version:
            try:
                self.bump_data_version()
            except Exception:
                pass
        photo_url = base_url.rstrip('/') + '/static/' + rel
        return {'photo': {'url': photo_url, 'id': photo_id, 'source': 'ai', 'name': filename, 'from': _text(url, 500)},
                'prompt': prompt}

    # -- ledger ---------------------------------------------------------------------------------

    def ledger(self, *, upc='', platform='', limit=200):
        """Every listing this panel linked, with the sales that traced back to it (sold.db orders)."""
        links = self.links(upc=upc, platform=platform, limit=limit)
        if not links:
            return []
        sales = {}
        try:
            with self.db('sold.db') as conn:
                cur = conn.cursor()
                if self._has_table(cur, 'orders'):
                    cur.execute('PRAGMA table_info(orders)')
                    columns = {r[1] for r in cur.fetchall()}
                    wanted = [c for c in ('id', 'order_id', 'store', 'item_id', 'sku', 'barcode', 'source_upc', 'listing_sku',
                                          'listing_listing_id', 'listing_asin', 'listing_trace_id', 'paid_time', 'shipped_time',
                                          'title', 'quantity', 'price') if c in columns]
                    if 'order_id' in columns:
                        keys = set()
                        for link in links:
                            for value in (link.get('listing_id'), link.get('sku'), link.get('asin')):
                                if _text(value):
                                    keys.add(_text(value))
                        if keys:
                            placeholders = ','.join('?' for _ in keys)
                            clauses = []
                            for col in ('item_id', 'sku', 'listing_sku', 'listing_listing_id', 'listing_asin'):
                                if col in columns:
                                    clauses.append(f"TRIM(COALESCE({col}, '')) IN ({placeholders})")
                            cur.execute(f"SELECT {', '.join(wanted)} FROM orders WHERE {' OR '.join(clauses)} ORDER BY paid_time DESC LIMIT 500",
                                        tuple(keys) * len(clauses))
                            for r in cur.fetchall():
                                r = _row(r)
                                for col in ('item_id', 'sku', 'listing_sku', 'listing_listing_id', 'listing_asin'):
                                    key = _text(r.get(col))
                                    if key in keys:
                                        sales.setdefault(key, []).append(r)
        except sqlite3.Error:
            pass
        out = []
        for link in links:
            matched = []
            seen = set()
            for value in (link.get('listing_id'), link.get('sku'), link.get('asin')):
                for order in sales.get(_text(value), []):
                    marker = (order.get('order_id'), order.get('item_id'), order.get('sku'))
                    if marker in seen:
                        continue
                    seen.add(marker)
                    matched.append(order)
            entry = dict(link)
            entry['sales'] = matched
            entry['traced'] = [o for o in matched if _text(o.get('source_upc')) == link['upc'] or _text(o.get('listing_trace_id'))]
            out.append(entry)
        return out

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
    _listagent_init_tables and optionally _listing_helper_scan_cache_clear. The queue-driven panel (0.2) also
    uses api_listingagent_upc_detail, _agent_build_proposal, _listagent_remove_from_queue,
    _listing_center_mark_bol_listed, _listagent_add_photo, update_data_version and BASE_DIR when present."""
    lister = Lister(deps, app.static_folder, app)

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

    # -- queue-driven panel (0.2) ------------------------------------------------------------------

    def api_lister_queue():
        try:
            result = lister.queue(platform=_text(request.args.get('platform') or 'ebay').lower(), base_url=base_url())
            return jsonify({'success': True, 'version': VERSION, **result})
        except Exception as e:
            return failure(e, 'lister:queue')

    def api_lister_queue_detail(upc):
        try:
            return jsonify({'success': True, 'item': lister.detail(upc, base_url=base_url())})
        except Exception as e:
            return failure(e, 'lister:detail')

    def api_lister_queue_prepare(upc):
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            result = lister.prepare(upc, base_url=base_url(), force=bool(data.get('force')), actor=actor())
            return jsonify({'success': True, **result})
        except Exception as e:
            return failure(e, 'lister:prepare')

    def api_lister_queue_skip(upc):
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            platform = _text(data.get('platform')).lower()
            if data.get('undo'):
                result = lister.unskip(upc, platform=platform)
            else:
                result = lister.skip(upc, platform=platform, actor=actor())
            return jsonify({'success': True, **result})
        except Exception as e:
            return failure(e, 'lister:skip')

    def api_lister_queue_mark_existing(upc):
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            result = lister.mark_existing(upc, platform=_text(data.get('platform')).lower(), entry=data.get('entry') or {},
                                          actor=actor(), base_url=base_url())
            return jsonify({'success': True, **result}), (200 if result.get('duplicate') else 201)
        except Exception as e:
            return failure(e, 'lister:mark-existing')

    def api_lister_voice_analyze(media_id):
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            return jsonify({'success': True, 'analysis': lister.analyze_voice(media_id, reanalyze=bool(data.get('reanalyze')))})
        except Exception as e:
            return failure(e, 'lister:voice')

    def api_lister_photo_ai():
        try:
            guard_mutation()
            data = request.get_json(silent=True) or {}
            result = lister.ai_photo(_text(data.get('upc')), url=_text(data.get('url'), 1000), prompt=data.get('prompt') or '',
                                     base_url=base_url(), quality=_text(data.get('quality')).lower() or 'medium')
            return jsonify({'success': True, **result}), 201
        except Exception as e:
            return failure(e, 'lister:photo-ai')

    def api_lister_photo_fetch():
        """The panel drags our photos onto store pages; the bytes come from here (same-origin safe)."""
        try:
            data, mime, name = lister.photo_bytes(_text(request.args.get('url'), 1000), base_url())
            return jsonify({'success': True, 'name': name, 'mime': mime, 'base64': base64.b64encode(data).decode('ascii')})
        except Exception as e:
            return failure(e, 'lister:photo-fetch')

    def api_lister_qr():
        text = _text(request.args.get('text'), 1000)
        if not text:
            return jsonify({'success': False, 'error': 'text is required'}), 400
        svg = qr_svg(text)
        if not svg:
            return jsonify({'success': False, 'error': 'QR codes need the qrcode package on the server.'}), 501
        return svg, 200, {'Content-Type': 'image/svg+xml; charset=utf-8', 'Cache-Control': 'no-store'}

    def api_lister_ledger():
        try:
            rows = lister.ledger(upc=_text(request.args.get('upc')), platform=_text(request.args.get('platform')).lower(),
                                 limit=request.args.get('limit') or 200)
            return jsonify({'success': True, 'links': rows})
        except Exception as e:
            return failure(e, 'lister:ledger')

    def lister_ledger_page():
        return render_template('lister_ledger.html')

    app.add_url_rule('/api/lister/queue', 'api_lister_queue', api_lister_queue)
    app.add_url_rule('/api/lister/queue/<upc>', 'api_lister_queue_detail', api_lister_queue_detail)
    app.add_url_rule('/api/lister/queue/<upc>/prepare', 'api_lister_queue_prepare', api_lister_queue_prepare, methods=['POST'])
    app.add_url_rule('/api/lister/queue/<upc>/skip', 'api_lister_queue_skip', api_lister_queue_skip, methods=['POST'])
    app.add_url_rule('/api/lister/queue/<upc>/mark-existing', 'api_lister_queue_mark_existing', api_lister_queue_mark_existing, methods=['POST'])
    app.add_url_rule('/api/lister/voice/<int:media_id>/analyze', 'api_lister_voice_analyze', api_lister_voice_analyze, methods=['POST'])
    app.add_url_rule('/api/lister/photos/ai', 'api_lister_photo_ai', api_lister_photo_ai, methods=['POST'])
    app.add_url_rule('/api/lister/photos/fetch', 'api_lister_photo_fetch', api_lister_photo_fetch)
    app.add_url_rule('/api/lister/qr', 'api_lister_qr', api_lister_qr)
    app.add_url_rule('/api/lister/ledger', 'api_lister_ledger', api_lister_ledger)
    app.add_url_rule('/lister-ledger', 'lister_ledger_page', lister_ledger_page)

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
