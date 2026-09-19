"""Sweet Shelves Lister: the Facebook Marketplace list ("FB" in the side panel).

Facebook has no listing API for individual sellers, so the Facebook list works differently from
eBay and Amazon. Nothing is filled on a store page. Each item is reviewed in the panel (title,
whole-dollar price, one of Facebook's four conditions, a Facebook category, a plain-text
description) and marked ready. Up to 50 ready items then go into Facebook's own bulk-upload
workbook (fb_lister_template.py), which is uploaded on Facebook by hand. Once the upload went
through, "Mark listed" records every item of that workbook as listed on Facebook: fbstore.db's
fb_listings (the Facebook tracking the rest of the app reads) and the BOL row's listed_facebook flag.

The list is opt-in: an item joins it only when "+" is pressed on Items to List while the panel
shows FB. A new item added that way is kept off the eBay and Amazon lists, the same way "+"
works for one store. eBay and Amazon never wait on Facebook.

Statuses: queued (added, not reviewed) -> ready -> in_template (in a built workbook) -> listed,
or removed. Shared verbatim with the Desktop debby app through lister_routes.register.
"""

import datetime
import io
import json
import re
import statistics
import zipfile
from pathlib import Path
from urllib.parse import quote

from flask import Response, jsonify, request

import fb_lister_template as tpl
from lister_routes import MUTATION_HEADER, PLATFORMS, ListerError, _absolute, _text, _thumb_url, html_to_text

PLATFORM = 'fb'
OPEN = ('queued', 'ready', 'in_template')
UPLOAD_URL = 'https://www.facebook.com/marketplace/create/bulk'
SEARCH_URL = 'https://www.google.com/search?q='
HELP_URL = 'https://www.facebook.com/help/1943158472539049'
PHOTO_LIMIT = 10
PHOTO_MAX_BYTES = 15_000_000


def _now():
    return datetime.datetime.now().isoformat(timespec='seconds')


def _row(row):
    return dict(row) if row is not None else None


class FbList:
    def __init__(self, lister, deps):
        self.lister = lister
        self.db = lister.db
        self.track_listing = deps.get('_track_fb_listing')
        # Price suggestions: live eBay comps (the app's own comps view) and Amazon's current offers (SP-API).
        self.ebay_comps_view = deps.get('api_listingagent_ebay_comps')
        self.amazon_context = deps.get('_amazon_spapi_context')
        base = deps.get('BASE_DIR')
        self.base_dir = Path(base) if base else Path('.')

    # -- storage --------------------------------------------------------------------------

    @staticmethod
    def init_tables(cur):
        cur.execute('''CREATE TABLE IF NOT EXISTS lister_fb_queue (
            upc TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'queued',
            title TEXT, price INTEGER, condition TEXT, description TEXT, category TEXT,
            reviewed INTEGER DEFAULT 0,
            batch_id INTEGER,
            added_at TEXT, updated_at TEXT, ready_at TEXT, listed_at TEXT,
            actor TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS lister_fb_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL DEFAULT 'built',
            rows_json TEXT,
            created_at TEXT, actor TEXT, listed_at TEXT)''')

    def _cursor(self, conn):
        cur = conn.cursor()
        self.init_tables(cur)
        return cur

    def key(self, upc):
        raw = _text(upc)
        return (self.lister.format_upc12(raw) if callable(getattr(self.lister, 'format_upc12', None)) else '') or raw

    def _find(self, cur, upc):
        variants = list(dict.fromkeys([self.key(upc), _text(upc), *self.lister._variants(_text(upc))]))
        marks = ','.join('?' for _ in variants)
        cur.execute(f'SELECT * FROM lister_fb_queue WHERE upc IN ({marks}) ORDER BY updated_at DESC', tuple(variants))
        return _row(cur.fetchone())

    # -- the store states Items to List shows -----------------------------------------------

    def merge_states(self, out):
        """Add 'fb' ('on' / 'listed') to lister.store_states() entries that have a Facebook row."""
        if not out:
            return out
        with self.db('listagent.db') as conn:
            cur = self._cursor(conn)
            for raw, entry in out.items():
                row = self._find(cur, raw)
                if row and row['status'] == 'listed':
                    entry[PLATFORM] = 'listed'
                elif row and row['status'] in OPEN:
                    entry[PLATFORM] = 'on'
        return out

    def set_store(self, upc, *, on, only=False, fresh=False, actor=''):
        raw = _text(upc)
        if not raw:
            raise ListerError('upc is required')
        key, now = self.key(raw), _now()
        with self.db('listagent.db') as conn:
            cur = self._cursor(conn)
            row = self._find(cur, raw)
            if on:
                if row is None:
                    cur.execute('INSERT INTO lister_fb_queue (upc, status, added_at, updated_at, actor) VALUES (?, ?, ?, ?, ?)',
                                (key, 'queued', now, now, actor))
                elif row['status'] == 'removed':
                    cur.execute("UPDATE lister_fb_queue SET status = 'queued', batch_id = NULL, updated_at = ?, actor = ? WHERE upc = ?",
                                (now, actor, row['upc']))
            elif row is not None and row['status'] in OPEN:
                cur.execute("UPDATE lister_fb_queue SET status = 'removed', batch_id = NULL, updated_at = ? WHERE upc = ?",
                            (now, row['upc']))
            conn.commit()
        # A brand-new entry added while the panel shows FB is for Facebook only, as "+" does for one store.
        if on and only and fresh:
            for platform in PLATFORMS:
                try:
                    self.lister.skip(raw, platform=platform, actor=actor)
                except ListerError:
                    pass
        return self.lister.store_states([raw])[raw]

    # -- the list -------------------------------------------------------------------------

    def _draft(self, row, info):
        """The values to review: what was saved, else what the eBay/Amazon proposal and notes say."""
        fields = (info or {}).get('fields') or {}
        title = row.get('title') or _text(fields.get('title') or (info or {}).get('title'), tpl.TITLE_MAX)
        description = row.get('description')
        if description is None:
            description = _text(fields.get('descriptionText')) and str(fields.get('descriptionText')) or html_to_text(fields.get('descriptionHtml') or '')
        category = row.get('category')
        suggestions = tpl.suggest_categories(title, fields.get('categoryPath') or '')
        if not category and suggestions:
            category = suggestions[0]['category']
        return {
            'title': title,
            'price': row.get('price') if row.get('price') is not None else tpl.whole_dollars(fields.get('price')),
            'condition': row.get('condition') or tpl.fb_condition(fields.get('condition') or (info or {}).get('condition')),
            'description': tpl.plain_text(description, tpl.DESCRIPTION_MAX),
            'category': category or '',
            'suggestions': [s['category'] for s in suggestions],
        }

    def queue(self, *, base_url='http://localhost/'):
        with self.db('listagent.db') as conn:
            cur = self._cursor(conn)
            cur.execute("SELECT * FROM lister_fb_queue WHERE status IN ('queued', 'ready', 'in_template') ORDER BY added_at, upc")
            rows = [_row(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM lister_fb_batches WHERE status = 'built' ORDER BY id DESC")
            batches = [_row(r) for r in cur.fetchall()]
            cur.execute("SELECT COUNT(*) FROM lister_fb_queue WHERE status = 'listed' AND listed_at >= ?",
                        (datetime.date.today().isoformat(),))
            listed_today = cur.fetchone()[0]
        thumbs = {}
        try:
            thumbs = self.lister._thumbs([r['upc'] for r in rows]) or {}
        except Exception:
            thumbs = {}
        items = []
        for row in rows:
            thumb = thumbs.get(row['upc']) if isinstance(thumbs, dict) else None
            if isinstance(thumb, dict):
                thumb = thumb.get('url') or thumb.get('thumb')
            items.append({
                'upc': row['upc'], 'status': row['status'], 'title': row['title'] or '', 'price': row['price'],
                'condition': row['condition'] or '', 'category': row['category'] or '', 'reviewed': bool(row['reviewed']),
                'batchId': row['batch_id'], 'addedAt': row['added_at'],
                'thumb': _thumb_url(thumb, base_url) if thumb else '',
                'problems': tpl.validate_row(row) if row['reviewed'] else [],
            })
        counts = {s: sum(1 for i in items if i['status'] == s) for s in OPEN}
        counts['queued_total'] = len(items)
        return {'items': items, 'counts': counts, 'listedToday': listed_today,
                'batches': [{'id': b['id'], 'createdAt': b['created_at'], 'count': len(json.loads(b['rows_json'] or '[]'))}
                            for b in batches],
                'uploadUrl': UPLOAD_URL, 'helpUrl': HELP_URL, 'maxRows': tpl.MAX_ROWS}

    def item(self, upc, *, base_url='http://localhost/'):
        with self.db('listagent.db') as conn:
            row = self._find(self._cursor(conn), upc)
        if row is None:
            raise ListerError('Not on the Facebook list', 404)
        try:
            info = self.lister.detail(row['upc'], base_url=base_url)
        except Exception:
            info = {}
        draft = self._draft(row, info)
        photos = []
        for photo in (info or {}).get('photos') or []:
            url = _text((photo or {}).get('url'))
            if url:
                photos.append({'url': _absolute(url, base_url), 'thumb': _thumb_url(url, base_url), 'source': photo.get('source') or ''})
        return {
            'upc': row['upc'], 'status': row['status'], 'reviewed': bool(row['reviewed']), 'batchId': row['batch_id'],
            'draft': draft, 'photos': photos[:24], 'conditions': list(tpl.CONDITIONS),
            'notes': (info or {}).get('notes') or '', 'defect': (info or {}).get('defect') or '',
            'racks': ((info or {}).get('fields') or {}).get('racks') or [],
            'limits': {'title': tpl.TITLE_MAX, 'description': tpl.DESCRIPTION_MAX},
            'problems': tpl.validate_row(draft),
        }

    def save(self, upc, data, *, actor=''):
        with self.db('listagent.db') as conn:
            cur = self._cursor(conn)
            row = self._find(cur, upc)
            if row is None:
                raise ListerError('Not on the Facebook list', 404)
            if row['status'] in ('in_template', 'listed'):
                raise ListerError('This item is already in a Facebook workbook. Put the workbook back first.', 409)
            values = {
                'title': _text(data.get('title'), tpl.TITLE_MAX),
                'price': tpl.whole_dollars(data.get('price')),
                'condition': _text(data.get('condition')),
                'description': tpl.plain_text(data.get('description'), tpl.DESCRIPTION_MAX),
                'category': _text(data.get('category'), 300),
            }
            ready = bool(data.get('ready'))
            problems = tpl.validate_row(values)
            if values['condition'] and values['condition'] not in tpl.CONDITIONS:
                raise ListerError('Condition must be one of: ' + ', '.join(tpl.CONDITIONS))
            if ready and problems:
                raise ListerError('Before it is ready, fix: ' + ', '.join(problems))
            now = _now()
            cur.execute('''UPDATE lister_fb_queue SET title = ?, price = ?, condition = ?, description = ?, category = ?,
                           reviewed = 1, status = ?, ready_at = ?, updated_at = ?, actor = ? WHERE upc = ?''',
                        (values['title'], values['price'], values['condition'], values['description'], values['category'],
                         'ready' if ready else 'queued', now if ready else None, now, actor, row['upc']))
            conn.commit()
        return {'upc': row['upc'], 'status': 'ready' if ready else 'queued', 'problems': problems}

    def remove(self, upc, *, actor=''):
        return self.set_store(upc, on=False, actor=actor)

    # -- workbooks ------------------------------------------------------------------------

    def build(self, upcs=None, *, actor=''):
        with self.db('listagent.db') as conn:
            cur = self._cursor(conn)
            cur.execute("SELECT * FROM lister_fb_queue WHERE status = 'ready' ORDER BY ready_at, upc")
            rows = [_row(r) for r in cur.fetchall()]
            if upcs:
                wanted = {self.key(u) for u in upcs}
                rows = [r for r in rows if r['upc'] in wanted]
            rows = rows[:tpl.MAX_ROWS]
            if not rows:
                raise ListerError('No items are ready. Review an item and press Ready first.')
            snapshot = [{k: r[k] for k in ('upc', 'title', 'price', 'condition', 'description', 'category')} for r in rows]
            tpl.build_workbook(snapshot)  # validates before anything is marked
            now = _now()
            cur.execute('INSERT INTO lister_fb_batches (status, rows_json, created_at, actor) VALUES (?, ?, ?, ?)',
                        ('built', json.dumps(snapshot), now, actor))
            batch_id = cur.lastrowid
            cur.executemany("UPDATE lister_fb_queue SET status = 'in_template', batch_id = ?, updated_at = ? WHERE upc = ?",
                            [(batch_id, now, r['upc']) for r in rows])
            conn.commit()
        return {'batchId': batch_id, 'count': len(rows), 'download': f'/api/lister/fb/batch/{batch_id}.xlsx'}

    def _batch(self, cur, batch_id):
        cur.execute('SELECT * FROM lister_fb_batches WHERE id = ?', (int(batch_id),))
        batch = _row(cur.fetchone())
        if batch is None:
            raise ListerError('No such Facebook workbook', 404)
        return batch

    def workbook(self, batch_id):
        with self.db('listagent.db') as conn:
            batch = self._batch(self._cursor(conn), batch_id)
        return tpl.build_workbook(json.loads(batch['rows_json'] or '[]'))

    def csv_file(self, batch_id):
        with self.db('listagent.db') as conn:
            batch = self._batch(self._cursor(conn), batch_id)
        return tpl.build_csv(json.loads(batch['rows_json'] or '[]'))

    # -- price suggestions ----------------------------------------------------------------

    def prices(self, upc):
        """What the item sells for elsewhere right now: eBay comps and Amazon offers, plus a web search link.

        Each source reports its own error instead of failing the call, so one dead API never hides the other."""
        with self.db('listagent.db') as conn:
            row = self._find(self._cursor(conn), upc)
        if row is None:
            raise ListerError('Not on the Facebook list', 404)
        base = str(row['upc']).split('-', 1)[0].strip()
        sources = [self._ebay_prices(base), self._amazon_prices(base)]
        return {'upc': row['upc'], 'searchUrl': SEARCH_URL + quote(base), 'sources': sources}

    def _ebay_prices(self, base):
        out = {'source': 'ebay', 'label': 'eBay', 'url': 'https://www.ebay.com/sch/i.html?_nkw=' + quote(base) + '&LH_BIN=1',
               'count': 0, 'low': None, 'median': None, 'high': None, 'suggested': None, 'error': ''}
        if not self.ebay_comps_view or not getattr(self.lister, 'app', None):
            out['error'] = 'eBay comps are not available on this server.'
            return out
        try:
            data = self.lister._call_view(self.ebay_comps_view, '/api/listingagent/ebay/comps',
                                          {'upc': base, 'limit': 20, 'sort': 'price'})
        except Exception as e:
            out['error'] = _text(str(e), 200) or 'eBay comps failed'
            return out
        if not data.get('success', True) and data.get('error'):
            out['error'] = _text(data.get('error'), 200)
            return out
        prices = []
        for comp in data.get('results') or data.get('comps') or []:
            value = ((comp or {}).get('price') or {}).get('value') if isinstance((comp or {}).get('price'), dict) else (comp or {}).get('price')
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if value > 0:
                prices.append(round(value, 2))
        prices.sort()
        if prices:
            out.update({'count': len(prices), 'low': prices[0], 'high': prices[-1],
                        'median': round(statistics.median(prices), 2), 'suggested': tpl.whole_dollars(statistics.median(prices))})
        return out

    def _amazon_prices(self, base):
        out = {'source': 'amazon', 'label': 'Amazon', 'url': 'https://www.amazon.com/s?k=' + quote(base),
               'asin': '', 'title': '', 'lowest': None, 'buyBox': None, 'count': 0, 'suggested': None, 'error': ''}
        search_view = getattr(self.lister, 'amazon_catalog_view', None)
        if not search_view or not self.amazon_context or not getattr(self.lister, 'app', None):
            out['error'] = 'Amazon pricing is not available on this server.'
            return out
        try:
            search = self.lister._call_view(search_view, '/api/listingagent/amazon/catalog_search', {'upc': base, 'mode': 'upc', 'limit': 3})
        except Exception as e:
            out['error'] = _text(str(e), 200) or 'Amazon catalog search failed'
            return out
        if not search.get('success'):
            out['error'] = _text(search.get('error'), 200) or 'Amazon catalog search failed'
            return out
        hits = [h for h in (search.get('results') or []) if _text(h.get('asin'))]
        if not hits:
            out['error'] = 'No Amazon product carries this barcode.'
            return out
        out['asin'] = _text(hits[0].get('asin')).upper()
        out['title'] = _text(hits[0].get('title'), 200)
        out['url'] = 'https://www.amazon.com/dp/' + out['asin']
        try:
            out.update(self._amazon_offers(out['asin']))
        except Exception as e:
            out['error'] = _text(str(e), 200) or 'Amazon offers failed'
        return out

    def _amazon_offers(self, asin):
        """Lowest landed price and the Buy Box for new offers (Product Pricing API)."""
        from sp_api.api import Products

        credentials, _, marketplace_id, marketplace = self.amazon_context()
        result = Products(credentials=credentials, marketplace=marketplace).get_item_offers(asin, ItemCondition='New')
        summary = (result.payload or {}).get('Summary') or {}
        found = {'lowest': None, 'buyBox': None, 'count': 0, 'suggested': None}

        def amount(entry):
            for key in ('LandedPrice', 'ListingPrice'):
                try:
                    value = float(((entry or {}).get(key) or {}).get('Amount'))
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    return round(value, 2)
            return None

        lowest = [amount(e) for e in summary.get('LowestPrices') or [] if str((e or {}).get('condition', '')).lower() == 'new']
        lowest = [v for v in lowest if v]
        if lowest:
            found['lowest'] = min(lowest)
        box = [amount(e) for e in summary.get('BuyBoxPrices') or [] if str((e or {}).get('condition', '')).lower() == 'new']
        box = [v for v in box if v]
        if box:
            found['buyBox'] = min(box)
        for entry in summary.get('NumberOfOffers') or []:
            if str((entry or {}).get('condition', '')).lower() == 'new':
                try:
                    found['count'] += int(entry.get('OfferCount') or 0)
                except (TypeError, ValueError):
                    pass
        pick = found['buyBox'] or found['lowest']
        found['suggested'] = tpl.whole_dollars(pick) if pick else None
        return found

    def batch_listed(self, batch_id, *, actor=''):
        with self.db('listagent.db') as conn:
            cur = self._cursor(conn)
            batch = self._batch(cur, batch_id)
            if batch['status'] != 'built':
                raise ListerError('This workbook was already ' + batch['status'], 409)
            rows = json.loads(batch['rows_json'] or '[]')
            now = _now()
            cur.execute("UPDATE lister_fb_batches SET status = 'listed', listed_at = ? WHERE id = ?", (now, batch['id']))
            cur.execute("UPDATE lister_fb_queue SET status = 'listed', listed_at = ?, updated_at = ? WHERE batch_id = ? AND status = 'in_template'",
                        (now, now, batch['id']))
            conn.commit()
        recorded, problems = 0, []
        for row in rows:
            upc = row['upc']
            try:
                if callable(self.track_listing):
                    self.track_listing(upc, quantity=1, listed=True, title=row.get('title'))
                    recorded += 1
            except Exception as e:
                problems.append(f'{upc}: {e}')
            try:
                if callable(self.lister.mark_bol_listed):
                    self.lister.mark_bol_listed('facebook', upc=upc)
            except Exception as e:
                problems.append(f'{upc}: {e}')
        return {'batchId': batch['id'], 'listed': len(rows), 'recorded': recorded, 'problems': problems}

    def batch_cancel(self, batch_id):
        """The upload did not happen: the items go back to ready and can be put in a new workbook."""
        with self.db('listagent.db') as conn:
            cur = self._cursor(conn)
            batch = self._batch(cur, batch_id)
            if batch['status'] != 'built':
                raise ListerError('This workbook was already ' + batch['status'], 409)
            now = _now()
            cur.execute("UPDATE lister_fb_batches SET status = 'cancelled' WHERE id = ?", (batch['id'],))
            cur.execute("UPDATE lister_fb_queue SET status = 'ready', batch_id = NULL, updated_at = ? WHERE batch_id = ? AND status = 'in_template'",
                        (now, batch['id']))
            conn.commit()
        return {'batchId': batch['id'], 'status': 'cancelled'}

    # -- photos: Facebook's workbook has none, they are added on each listing afterwards ----

    def photos_zip(self, upc, *, base_url='http://localhost/'):
        detail = self.item(upc, base_url=base_url)
        buffer = io.BytesIO()
        added = 0
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_STORED) as bundle:
            for index, photo in enumerate(detail['photos'][:PHOTO_LIMIT], start=1):
                data = self._photo_bytes(photo['url'], base_url)
                if not data:
                    continue
                ext = '.png' if data[:4] == b'\x89PNG' else ('.webp' if data[8:12] == b'WEBP' else '.jpg')
                bundle.writestr(f'{detail["upc"]}-{index:02d}{ext}', data)
                added += 1
        if not added:
            raise ListerError('This item has no photos to save.', 404)
        return buffer.getvalue(), detail['upc']

    def _photo_bytes(self, url, base_url):
        root = base_url.rstrip('/')
        if url.startswith(root + '/static/'):
            local = (self.base_dir / 'static' / url[len(root) + len('/static/'):].split('?')[0]).resolve()
            static = (self.base_dir / 'static').resolve()
            if static in local.parents and local.is_file() and local.stat().st_size <= PHOTO_MAX_BYTES:
                return local.read_bytes()
            return None
        if not re.match(r'^https://', url):
            return None
        try:
            import requests
            response = requests.get(url, timeout=(4, 15), stream=True)
            if not response.ok:
                return None
            data = response.raw.read(PHOTO_MAX_BYTES + 1, decode_content=True)
            return data if data and len(data) <= PHOTO_MAX_BYTES else None
        except Exception:
            return None


def register(app, lister, deps):
    """Wire the Facebook list beside the rest of the Lister (called from lister_routes.register)."""
    fb = FbList(lister, deps)
    lister.fb_list = fb

    def base_url():
        try:
            root = (request.url_root or '').strip() or 'http://localhost/'
            proto = _text(request.headers.get('X-Forwarded-Proto')).split(',')[0].strip().lower()
            if proto == 'https' and root.startswith('http://'):
                root = 'https://' + root[len('http://'):]
            return root
        except Exception:
            return 'http://localhost/'

    def actor():
        data = request.get_json(silent=True) if request.method == 'POST' else None
        value = _text((data or {}).get('actor'), 80)
        return value or _text(request.headers.get('Cf-Access-Authenticated-User-Email'), 120) or 'lister'

    def guard():
        if request.headers.get('Sec-Fetch-Site') == 'cross-site' and not request.headers.get(MUTATION_HEADER):
            raise ListerError('Use the Sweet Shelves Lister extension or a Sweet Shelves page for this action.', 403)

    def failure(e, context):
        if isinstance(e, ListerError):
            return jsonify({'success': False, 'error': str(e)}), e.status
        if isinstance(e, ValueError):
            return jsonify({'success': False, 'error': str(e)}), 400
        return jsonify({'success': False, 'error': lister.safe_error(e, context)}), 500

    def api_lister_fb_queue():
        try:
            return jsonify({'success': True, **fb.queue(base_url=base_url())})
        except Exception as e:
            return failure(e, 'lister:fb:queue')

    def api_lister_fb_item(upc):
        try:
            if request.method == 'POST':
                guard()
                return jsonify({'success': True, **fb.save(upc, request.get_json(silent=True) or {}, actor=actor())})
            return jsonify({'success': True, **fb.item(upc, base_url=base_url())})
        except Exception as e:
            return failure(e, 'lister:fb:item')

    def api_lister_fb_prices(upc):
        try:
            return jsonify({'success': True, **fb.prices(upc)})
        except Exception as e:
            return failure(e, 'lister:fb:prices')

    def api_lister_fb_remove(upc):
        try:
            guard()
            return jsonify({'success': True, 'state': fb.remove(upc, actor=actor())})
        except Exception as e:
            return failure(e, 'lister:fb:remove')

    def api_lister_fb_categories():
        try:
            q = _text(request.args.get('q'), 120).lower()
            words = [w for w in q.split() if w]
            found = [c for c in tpl.categories() if all(w in c.lower() for w in words)] if words else []
            return jsonify({'success': True, 'categories': found[:30],
                            'suggested': [s['category'] for s in tpl.suggest_categories(q)] if q else []})
        except Exception as e:
            return failure(e, 'lister:fb:categories')

    def api_lister_fb_build():
        try:
            guard()
            data = request.get_json(silent=True) or {}
            upcs = data.get('upcs')
            if upcs is not None and not isinstance(upcs, list):
                raise ListerError('upcs must be a list')
            return jsonify({'success': True, **fb.build(upcs, actor=actor())})
        except Exception as e:
            return failure(e, 'lister:fb:build')

    def api_lister_fb_batch_file(batch_id):
        try:
            data = fb.workbook(batch_id)
            name = f'facebook-marketplace-{int(batch_id)}.xlsx'
            return Response(data, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                            headers={'Content-Disposition': f'attachment; filename="{name}"', 'Cache-Control': 'no-store'})
        except Exception as e:
            return failure(e, 'lister:fb:workbook')

    def api_lister_fb_batch_csv(batch_id):
        try:
            data = fb.csv_file(batch_id)
            name = f'facebook-marketplace-{int(batch_id)}.csv'
            return Response(data, mimetype='text/csv',
                            headers={'Content-Disposition': f'attachment; filename="{name}"', 'Cache-Control': 'no-store'})
        except Exception as e:
            return failure(e, 'lister:fb:csv')

    def api_lister_fb_batch_listed(batch_id):
        try:
            guard()
            return jsonify({'success': True, **fb.batch_listed(batch_id, actor=actor())})
        except Exception as e:
            return failure(e, 'lister:fb:listed')

    def api_lister_fb_batch_cancel(batch_id):
        try:
            guard()
            return jsonify({'success': True, **fb.batch_cancel(batch_id)})
        except Exception as e:
            return failure(e, 'lister:fb:cancel')

    def api_lister_fb_photos(upc):
        try:
            data, key = fb.photos_zip(upc, base_url=base_url())
            return Response(data, mimetype='application/zip',
                            headers={'Content-Disposition': f'attachment; filename="{key}-photos.zip"', 'Cache-Control': 'no-store'})
        except Exception as e:
            return failure(e, 'lister:fb:photos')

    app.add_url_rule('/api/lister/fb/queue', 'api_lister_fb_queue', api_lister_fb_queue)
    app.add_url_rule('/api/lister/fb/categories', 'api_lister_fb_categories', api_lister_fb_categories)
    app.add_url_rule('/api/lister/fb/item/<upc>', 'api_lister_fb_item', api_lister_fb_item, methods=['GET', 'POST'])
    app.add_url_rule('/api/lister/fb/item/<upc>/remove', 'api_lister_fb_remove', api_lister_fb_remove, methods=['POST'])
    app.add_url_rule('/api/lister/fb/item/<upc>/prices', 'api_lister_fb_prices', api_lister_fb_prices)
    app.add_url_rule('/api/lister/fb/item/<upc>/photos.zip', 'api_lister_fb_photos', api_lister_fb_photos)
    app.add_url_rule('/api/lister/fb/build', 'api_lister_fb_build', api_lister_fb_build, methods=['POST'])
    app.add_url_rule('/api/lister/fb/batch/<int:batch_id>.xlsx', 'api_lister_fb_batch_file', api_lister_fb_batch_file)
    app.add_url_rule('/api/lister/fb/batch/<int:batch_id>.csv', 'api_lister_fb_batch_csv', api_lister_fb_batch_csv)
    app.add_url_rule('/api/lister/fb/batch/<int:batch_id>/listed', 'api_lister_fb_batch_listed', api_lister_fb_batch_listed,
                     methods=['POST'])
    app.add_url_rule('/api/lister/fb/batch/<int:batch_id>/cancel', 'api_lister_fb_batch_cancel', api_lister_fb_batch_cancel,
                     methods=['POST'])
    return fb
