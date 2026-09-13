"""Shared physical, pending-sale and available stock view. No inventory writes."""
from collections import Counter, defaultdict
import datetime
import hashlib
import json


def _int(value, default=0):
    try:
        return int(float(value))
    except (ValueError, TypeError, OverflowError):
        return default


def _recent(order):
    if _int(order.get('rackupdated')) or _int(order.get('removal_cancelled')):
        return False
    if str(order.get('store', '')).lower() == 'test':
        return False
    channel = str(order.get('fulfillment_channel') or '').upper()
    if _int(order.get('is_fba')) or channel == 'AFN' or channel.startswith('AMAZON'):
        return False
    for field in ('shipped_time', 'paid_time'):
        try:
            stamp = datetime.datetime.fromisoformat(str(order.get(field) or '').replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=datetime.timezone.utc)
            return (datetime.datetime.now(datetime.timezone.utc)-stamp).total_seconds() <= 7*86400
        except (ValueError, TypeError):
            continue
    return True


def _review_hash(identity, status, physical, pending, qty, rows):
    return hashlib.sha256(json.dumps(['stock-review-v1', identity, status, physical, pending, qty,
                                      sorted(rows)], sort_keys=True).encode()).hexdigest()


def apply(entries, rack, orders, reviewed_hashes, facebook_codes, key):
    groups = defaultdict(list)
    by_id = {r['id']:r for r in rack}
    for row in rack:
        groups[key(row['barcode'])].append(row)
    identity = {}
    covered = set(facebook_codes)
    for listing in entries:
        for field in ('listing_key', 'item_id', 'sku'):
            value = str(listing.get(field) or '').lower()
            if value:
                bucket = identity.setdefault((listing['store'], value), [])
                if listing not in bucket:
                    bucket.append(listing)
        covered.update(key(c) for c in listing.get('codes', []))
        covered.update(key(r['barcode']) for r in listing['warehouse'])

    pending_product, pending_row = Counter(), Counter()
    for order in orders:
        if not _recent(order):
            continue
        quantity = max(1, _int(order.get('quantity'), 1))
        code = key(order.get('barcode', ''))
        preferred = []
        linked = False
        store = str(order.get('store') or '').lower()
        for field in ('listing_listing_id', 'item_id', 'listing_sku', 'sku', 'listing_asin'):
            value = str(order.get(field) or '').lower()
            if value.startswith(store+':'):
                value = value[len(store)+1:]
            candidates = identity.get((store, value), [])
            # ASIN/item aliases can identify several offers; only use an unambiguous match.
            if len(candidates) == 1 and candidates[0]['warehouse']:
                preferred = [r['searchrack_id'] for r in candidates[0]['warehouse']]
                linked = candidates[0]['match_kind'] == 'linked'
                break
        if preferred and (linked or code not in groups):
            code = key(by_id[preferred[0]]['barcode'])
        if not code:
            continue
        pending_product[code] += quantity
        raw_code = str(order.get('barcode') or '').strip().lower()
        rows = sorted(groups.get(code, []), key=lambda r:(r['id'] not in preferred,
                                                        r['barcode'].lower() != raw_code, r['id']))
        remaining = quantity
        for row in rows:
            take = min(remaining, max(0, row['quantity']-pending_row[row['id']]))
            pending_row[row['id']] += take
            remaining -= take
        if remaining and rows:
            pending_row[rows[0]['id']] += remaining

    for listing in entries:
        physical = listing['warehouse_qty']
        pending = sum(pending_row[w['searchrack_id']] for w in listing['warehouse'])
        if not listing['warehouse']:
            pending = sum(pending_product[c] for c in {key(c) for c in listing.get('codes', [])})
        known = bool(listing['warehouse']) or listing['state'] == 'sold_out' or pending > 0
        available = max(0, physical-pending) if known else None
        if listing['state'] == 'handled':
            status = 'reviewed'
        elif not known:
            status = 'needs_matching'
        elif available == 0:
            status = 'out_of_stock'
        elif available < listing['qty']:
            status = 'short_stock'
        else:
            status = 'in_stock'
        review_hash = _review_hash(listing['listing_id'], status, physical, pending, listing['qty'],
                                   [(w['searchrack_id'],w['barcode']) for w in listing['warehouse']])
        listing.update(physical_qty=physical if known else None, pending_sale_qty=pending,
                       available_qty=available, stock_status=status, review_hash=review_hash,
                       available_short_by=max(0,listing['qty']-available) if available is not None else None)
        if listing['state'] == 'handled':
            listing['review_hash'] = listing['hash']
        elif status != 'in_stock' and review_hash in reviewed_hashes:
            listing['reviewed_status'] = status
            listing['stock_status'] = 'reviewed'

    unlisted = []
    for code, rows in sorted(groups.items()):
        if code in covered:
            continue
        physical = sum(r['quantity'] for r in rows)
        pending = pending_product[code]
        first = rows[0]
        review_hash = _review_hash('warehouse:'+code, 'unlisted', physical, pending, 0,
                                   [(r['id'],r['barcode']) for r in rows])
        unlisted.append(dict(store='warehouse', listing_key=code, listing_id='warehouse:'+code,
            title=first['display_title'], upc=first['barcode'], sku='', hint='', qty=0,
            image=next((r['image'] for r in rows if r['image']), ''), url='',
            state='warehouse_unlisted', stock_status='reviewed' if review_hash in reviewed_hashes else 'unlisted',
            hash=review_hash, review_hash=review_hash, warehouse_qty=physical, physical_qty=physical,
            pending_sale_qty=pending, available_qty=max(0,physical-pending), short_by=0,
            suggestions=[], match_kind='', received=False, codes=[first['barcode']],
            warehouse=[dict(searchrack_id=r['id'], barcode=r['barcode'], title=r['display_title'],
                            location=r['location'], quantity=r['quantity'], note=r['note'],
                            alternate_titles=r.get('alternate_titles', [])) for r in rows]))
    counts = Counter(l['stock_status'] for l in entries+unlisted)
    return unlisted, dict(counts)
