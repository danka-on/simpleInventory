"""Profit and loss built from real payouts, lot purchases, orders, returns and expenses.

Two views are produced from the local databases, read-only:

* ``cash``  - money that actually moved: closed Amazon/eBay payouts plus recorded cash
  sales in, lot goods + freight + other expenses out, month by month.
* ``lots``  - an estimated attribution of every sold order to the lot it came from,
  so each pallet can be judged on its own.  Orders whose UPC is on no manifest are
  kept in a separate "not on BOL" bucket instead of being dropped.

Nothing here writes to a database.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections import defaultdict
from pathlib import Path


PRICE_SANITY_LIMIT = 25000.0
EXCLUDED_STORES = {'test'}
CASH_SALE_STORES = {'marketplace'}
INVALID_LOT_VALUES = {'', 'nan', 'none', 'null'}


# ---------------------------------------------------------------------------
# helpers


def _connect_readonly(path):
    path = Path(path)
    if not path.exists():
        return None
    conn = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn, name):
    if conn is None:
        return False
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? COLLATE NOCASE", (name,)
    ).fetchone()
    return row is not None


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def money(value):
    try:
        if value is None:
            return 0.0
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def _quantity(value):
    try:
        qty = int(float(value))
    except (TypeError, ValueError):
        return 1
    return qty if qty > 0 else 1


def _text(value):
    if value is None:
        return ''
    return str(value).strip()


def valid_lot(value):
    text = _text(value)
    return text if text.lower() not in INVALID_LOT_VALUES else ''


def upc_key(value):
    """Normalise a barcode so suffixed units, float exports and leading zeros still match."""
    text = _text(value)
    if not text or text.lower() in INVALID_LOT_VALUES:
        return ''
    if text.endswith('.0'):
        text = text[:-2]
    base = text.split('-', 1)[0].strip()
    if not base:
        return ''
    digits = ''.join(ch for ch in base if ch.isdigit())
    if digits and digits == base.replace(' ', ''):
        return digits.lstrip('0') or '0'
    return base.lower()


def _month(value):
    text = _text(value)
    return text[:7] if len(text) >= 7 else ''


def _date(value):
    text = _text(value)
    return text[:10] if len(text) >= 10 else text


def _pct(part, whole):
    if not whole:
        return None
    return round(part / whole * 100.0, 1)


def _new_money_bucket():
    return {
        'orders': 0, 'units': 0, 'revenue': 0.0, 'fees': 0.0, 'shipping': 0.0,
        'refunds': 0.0, 'net_sales': 0.0,
    }


def _add_sale(bucket, sale):
    bucket['orders'] += 1
    bucket['units'] += sale['units']
    bucket['revenue'] = round(bucket['revenue'] + sale['revenue'], 2)
    bucket['fees'] = round(bucket['fees'] + sale['fees'], 2)
    bucket['shipping'] = round(bucket['shipping'] + sale['shipping'], 2)
    bucket['refunds'] = round(bucket['refunds'] + sale['refunds'], 2)
    bucket['net_sales'] = round(
        bucket['revenue'] - bucket['fees'] - bucket['shipping'] - bucket['refunds'], 2
    )


# ---------------------------------------------------------------------------
# loading


def _load_lots(rawbol):
    """Lots from upload_logs merged with the manifest rows that reference them."""
    lots = {}
    manifest = defaultdict(dict)
    upc_lots = defaultdict(list)
    orphan_manifest_rows = 0
    if rawbol is None:
        return lots, manifest, upc_lots, orphan_manifest_rows

    if _table_exists(rawbol, 'upload_logs'):
        cols = _columns(rawbol, 'upload_logs')
        for row in rawbol.execute('SELECT * FROM upload_logs'):
            lot = valid_lot(row['lot_number'])
            if not lot:
                continue
            entry = lots.setdefault(lot, {
                'lot_number': lot, 'import_date': '', 'filenames': [], 'rows_imported': 0,
                'goods_cost': None, 'freight': None, 'in_upload_logs': True,
            })
            entry['in_upload_logs'] = True
            import_date = _date(row['import_date'])
            if import_date and (not entry['import_date'] or import_date < entry['import_date']):
                entry['import_date'] = import_date
            filename = _text(row['filename']) if 'filename' in cols else ''
            if filename and filename not in entry['filenames']:
                entry['filenames'].append(filename)
            try:
                entry['rows_imported'] += int(row['rows_imported'] or 0)
            except (TypeError, ValueError):
                pass
            goods = row['total_client_cost'] if 'total_client_cost' in cols else None
            if goods is not None and _text(goods) != '':
                entry['goods_cost'] = round((entry['goods_cost'] or 0.0) + money(goods), 2)
            freight = row['shipping_cost'] if 'shipping_cost' in cols else None
            if freight is not None and _text(freight) != '':
                entry['freight'] = round((entry['freight'] or 0.0) + money(freight), 2)

    if _table_exists(rawbol, 'raw_bol_items'):
        cols = _columns(rawbol, 'raw_bol_items')
        retail_col = 'original_retail' if 'original_retail' in cols else None
        for row in rawbol.execute('SELECT * FROM raw_bol_items'):
            lot = valid_lot(row['lot_number'])
            key = upc_key(row['upc'])
            if not lot or not key:
                orphan_manifest_rows += 1
                continue
            entry = lots.setdefault(lot, {
                'lot_number': lot, 'import_date': '', 'filenames': [], 'rows_imported': 0,
                'goods_cost': None, 'freight': None, 'in_upload_logs': False,
            })
            import_date = _date(row['import_date'])
            if import_date and not entry['import_date']:
                entry['import_date'] = import_date
            qty = _quantity(row['quantity'])
            item = manifest[lot].setdefault(key, {'qty': 0, 'avg_cost': None, 'retail': None, 'title': ''})
            item['qty'] += qty
            if row['avg_cost'] is not None and item['avg_cost'] is None:
                item['avg_cost'] = money(row['avg_cost'])
            if retail_col and row[retail_col] is not None and item['retail'] is None:
                item['retail'] = money(row[retail_col])
            if not item['title']:
                item['title'] = _text(row['item_description'])
            if lot not in upc_lots[key]:
                upc_lots[key].append(lot)

    for lot, entry in lots.items():
        items = manifest.get(lot, {})
        entry['manifested_units'] = sum(item['qty'] for item in items.values())
        entry['manifested_skus'] = len(items)
        entry['retail_value'] = round(
            sum((item['retail'] or 0.0) * item['qty'] for item in items.values()), 2
        )
        cost_from_items = round(
            sum((item['avg_cost'] or 0.0) * item['qty'] for item in items.values()), 2
        )
        if entry['goods_cost'] is None or entry['goods_cost'] <= 0:
            entry['goods_cost'] = cost_from_items if cost_from_items > 0 else None
            entry['goods_cost_source'] = 'manifest rows' if cost_from_items > 0 else 'missing'
        else:
            entry['goods_cost_source'] = 'upload log'
        entry['freight_missing'] = entry['freight'] is None
        entry['landed_cost'] = round((entry['goods_cost'] or 0.0) + (entry['freight'] or 0.0), 2)
        units = entry['manifested_units']
        entry['unit_freight'] = round((entry['freight'] or 0.0) / units, 4) if units else 0.0

    for key, lot_list in upc_lots.items():
        lot_list.sort(key=lambda lot: lots[lot]['import_date'])
    return lots, manifest, upc_lots, orphan_manifest_rows


def _load_asin_map(amazon):
    asin_map = {}
    if amazon is None or not _table_exists(amazon, 'ITEMS'):
        return asin_map
    cols = _columns(amazon, 'ITEMS')
    if 'ASIN' not in cols or 'UPC' not in cols:
        return asin_map
    for row in amazon.execute('SELECT ASIN, UPC FROM ITEMS WHERE UPC IS NOT NULL'):
        asin = upc_key(row['ASIN'])
        upc = upc_key(row['UPC'])
        if asin and upc:
            asin_map.setdefault(asin, upc)
    return asin_map


def _pick_lot(candidates, lots, paid_time):
    if len(candidates) == 1:
        return candidates[0]
    paid = _date(paid_time)
    before = [lot for lot in candidates if lots[lot]['import_date'] and lots[lot]['import_date'] <= paid]
    if before:
        return before[-1]
    return candidates[0]


def _load_orders(sold, lots, manifest, upc_lots, asin_map, quality):
    sales = []
    if sold is None or not _table_exists(sold, 'orders'):
        return sales
    cols = _columns(sold, 'orders')
    optional = {name: (name in cols) for name in (
        'source_base_upc', 'source_upc', 'barcode', 'item_id', 'lot_number', 'seller_fee',
        'shipping_cost', 'store', 'quantity', 'sku', 'title', 'order_id',
    )}

    def col(row, name):
        return row[name] if optional.get(name) else None

    for row in sold.execute('SELECT * FROM orders WHERE paid_time IS NOT NULL'):
        store = (_text(col(row, 'store')) or 'ebay').lower()
        title = _text(col(row, 'title'))
        paid_time = _text(row['paid_time'])
        price = row['price']
        try:
            price_value = float(price)
        except (TypeError, ValueError):
            price_value = None
        base = {
            'id': row['id'], 'order_id': _text(col(row, 'order_id')), 'store': store,
            'title': title, 'price': money(price), 'paid_time': paid_time,
        }
        if store in EXCLUDED_STORES:
            quality['excluded_orders'].append(dict(base, reason='test store'))
            continue
        if price_value is None:
            quality['excluded_orders'].append(dict(base, reason='price is not a number'))
            continue
        if price_value < 0 or price_value > PRICE_SANITY_LIMIT:
            quality['excluded_orders'].append(
                dict(base, reason=f'price above ${PRICE_SANITY_LIMIT:,.0f} looks like a barcode typed as a price')
            )
            continue

        units = _quantity(col(row, 'quantity'))
        candidates = []
        for name in ('source_base_upc', 'barcode', 'source_upc', 'item_id'):
            key = upc_key(col(row, name))
            if key and key not in candidates:
                candidates.append(key)
        matched_key = ''
        for key in candidates:
            if key in upc_lots:
                matched_key = key
                break
            mapped = asin_map.get(key)
            if mapped and mapped in upc_lots:
                matched_key = mapped
                break

        explicit_lot = valid_lot(col(row, 'lot_number'))
        lot = explicit_lot
        attribution = 'assigned'
        if not lot and matched_key:
            lot = _pick_lot(upc_lots[matched_key], lots, paid_time)
            attribution = 'inferred'
        if not lot:
            attribution = 'unmatched'
        if lot and lot not in lots:
            lots[lot] = {
                'lot_number': lot, 'import_date': '', 'filenames': [], 'rows_imported': 0,
                'goods_cost': None, 'goods_cost_source': 'missing', 'freight': None,
                'freight_missing': True, 'in_upload_logs': False, 'manifested_units': 0,
                'manifested_skus': 0, 'retail_value': 0.0, 'landed_cost': 0.0, 'unit_freight': 0.0,
            }

        on_manifest = bool(lot and matched_key and matched_key in manifest.get(lot, {}))
        cost_item = None
        if on_manifest:
            cost_item = manifest[lot][matched_key]
        elif matched_key:
            for other in upc_lots.get(matched_key, []):
                cost_item = manifest[other].get(matched_key)
                if cost_item:
                    break
        avg_cost = cost_item['avg_cost'] if cost_item and cost_item['avg_cost'] is not None else None
        unit_freight = lots[lot]['unit_freight'] if lot else 0.0

        sales.append({
            'id': row['id'], 'order_id': base['order_id'], 'store': store, 'title': title,
            'paid_time': paid_time, 'month': _month(paid_time), 'units': units,
            'revenue': round(money(price) * units, 2), 'fees': money(col(row, 'seller_fee')),
            'shipping': money(col(row, 'shipping_cost')), 'refunds': 0.0, 'return_count': 0,
            'lot': lot, 'attribution': attribution, 'upc_key': matched_key or (candidates[0] if candidates else ''),
            'on_manifest': on_manifest, 'has_cost': avg_cost is not None,
            'cogs': round((avg_cost or 0.0) * units, 2),
            'freight_alloc': round(unit_freight * units, 2),
            'no_barcode': not candidates,
        })
    return sales


def _apply_returns(sold, sales, quality):
    unmatched = []
    if sold is None or not _table_exists(sold, 'returns'):
        quality['returns_table'] = False
        return unmatched
    quality['returns_table'] = True
    by_id = {sale['id']: sale for sale in sales}
    by_order_id = {}
    for sale in sales:
        if sale['order_id']:
            by_order_id.setdefault(sale['order_id'], sale)
    cols = _columns(sold, 'returns')
    seen = set()
    last_date = ''
    for row in sold.execute('SELECT * FROM returns ORDER BY id DESC'):
        signature = tuple(
            _text(row[name]) if name in cols else ''
            for name in ('original_order_id', 'order_id', 'item_id', 'barcode', 'title', 'return_date', 'store')
        )
        if signature in seen:
            continue
        seen.add(signature)
        return_date = _date(row['return_date']) if 'return_date' in cols else ''
        if return_date > last_date:
            last_date = return_date
        refund = money(row['refund_amount']) + money(row['return_shipping_cost'] if 'return_shipping_cost' in cols else 0)
        sale = None
        original = row['original_order_id'] if 'original_order_id' in cols else None
        if original is not None and _text(original) != '':
            try:
                sale = by_id.get(int(original))
            except (TypeError, ValueError):
                sale = None
        if sale is None and 'order_id' in cols:
            sale = by_order_id.get(_text(row['order_id']))
        if sale is not None:
            sale['refunds'] = round(sale['refunds'] + refund, 2)
            sale['return_count'] += 1
            sale['return_month'] = _month(return_date) or sale['month']
        else:
            unmatched.append({
                'store': (_text(row['store']) or 'unknown').lower() if 'store' in cols else 'unknown',
                'title': _text(row['title']) if 'title' in cols else '',
                'order_id': _text(row['order_id']) if 'order_id' in cols else '',
                'return_date': return_date,
                'amount': round(refund + money(row['original_shipping_cost'] if 'original_shipping_cost' in cols else 0), 2),
                'month': _month(return_date),
            })
    quality['returns_last_date'] = last_date
    return unmatched


def _load_payouts(sold, quality):
    rows = []
    if sold is None or not _table_exists(sold, 'payouts'):
        quality['payouts_table'] = False
        return rows
    quality['payouts_table'] = True
    cols = _columns(sold, 'payouts')
    last_synced = ''
    for row in sold.execute('SELECT * FROM payouts'):
        store = (_text(row['store']) or 'unknown').lower()
        status = _text(row['status']).lower() if 'status' in cols else 'closed'
        amount = money(row['amount'])
        when = ''
        for name in ('payout_date', 'end_date', 'start_date'):
            if name in cols and _text(row[name]):
                when = _text(row[name])
                break
        synced = _text(row['synced_at']) if 'synced_at' in cols else ''
        if synced > last_synced:
            last_synced = synced
        entry = {
            'store': store, 'status': status, 'amount': amount, 'date': _date(when), 'month': _month(when),
            'settlement_id': _text(row['settlement_id']) if 'settlement_id' in cols else '',
        }
        if status != 'closed':
            quality['open_settlements'].append(entry)
            continue
        if amount == 0:
            quality['zero_closed_settlements'].append(entry)
            continue
        rows.append(entry)
    quality['payouts_last_synced'] = last_synced
    return rows


def _load_expenses(sold):
    rows = []
    if sold is None or not _table_exists(sold, 'pnl_expenses'):
        return rows
    for row in sold.execute('SELECT * FROM pnl_expenses ORDER BY expense_date DESC, id DESC'):
        rows.append({
            'id': row['id'], 'expense_date': _date(row['expense_date']), 'month': _month(row['expense_date']),
            'category': _text(row['category']), 'amount': money(row['amount']), 'note': _text(row['note']),
        })
    return rows


def _load_on_hand(searchrack, lots, upc_lots):
    on_hand = defaultdict(int)
    unmatched_units = 0
    if searchrack is None or not _table_exists(searchrack, 'SEARCHRACK'):
        return on_hand, unmatched_units
    for row in searchrack.execute('SELECT BARCODE, QUANTITY FROM SEARCHRACK'):
        key = upc_key(row['BARCODE'])
        qty = _quantity(row['QUANTITY'])
        candidates = upc_lots.get(key)
        if not candidates:
            unmatched_units += qty
            continue
        on_hand[candidates[-1]] += qty
    return on_hand, unmatched_units


# ---------------------------------------------------------------------------
# building


def build_pnl(base_dir, now=None):
    base = Path(base_dir)
    now = now or dt.datetime.now()
    quality = {
        'excluded_orders': [], 'open_settlements': [], 'zero_closed_settlements': [],
        'returns_last_date': '', 'payouts_last_synced': '',
    }
    connections = {
        name: _connect_readonly(base / filename)
        for name, filename in (
            ('sold', 'sold.db'), ('rawbol', 'rawbol.db'), ('amazon', 'amazonStore.db'),
            ('searchrack', 'searchRack.db'),
        )
    }
    try:
        lots, manifest, upc_lots, orphan_rows = _load_lots(connections['rawbol'])
        asin_map = _load_asin_map(connections['amazon'])
        sales = _load_orders(connections['sold'], lots, manifest, upc_lots, asin_map, quality)
        unmatched_returns = _apply_returns(connections['sold'], sales, quality)
        payouts = _load_payouts(connections['sold'], quality)
        expenses = _load_expenses(connections['sold'])
        on_hand, on_hand_unmatched = _load_on_hand(connections['searchrack'], lots, upc_lots)
    finally:
        for conn in connections.values():
            if conn is not None:
                conn.close()

    # ---- lots ------------------------------------------------------------
    lot_rows = []
    for lot in sorted(lots.values(), key=lambda entry: (entry['import_date'] or '9999', entry['lot_number'])):
        number = lot['lot_number']
        bucket = _new_money_bucket()
        extra = _new_money_bucket()
        inferred = 0
        manifest_units_sold = 0
        cogs = 0.0
        freight_alloc = 0.0
        no_cost_orders = 0
        for sale in sales:
            if sale['lot'] != number:
                continue
            _add_sale(bucket, sale)
            if sale['attribution'] == 'inferred':
                inferred += 1
            if sale['on_manifest']:
                manifest_units_sold += sale['units']
            else:
                _add_sale(extra, sale)
            if not sale['has_cost']:
                no_cost_orders += 1
            cogs += sale['cogs']
            freight_alloc += sale['freight_alloc']
        cogs = round(cogs, 2)
        freight_alloc = round(freight_alloc, 2)
        units = lot['manifested_units']
        lot_rows.append({
            'lot_number': number,
            'import_date': lot['import_date'],
            'filenames': lot['filenames'],
            'in_upload_logs': lot['in_upload_logs'],
            'manifested_units': units,
            'manifested_skus': lot['manifested_skus'],
            'retail_value': lot['retail_value'],
            'goods_cost': lot['goods_cost'],
            'goods_cost_source': lot.get('goods_cost_source', 'missing'),
            'freight': lot['freight'],
            'freight_missing': lot['freight_missing'],
            'landed_cost': lot['landed_cost'],
            'sold': bucket,
            'extra': extra,
            'inferred_orders': inferred,
            'no_cost_orders': no_cost_orders,
            'manifest_units_sold': manifest_units_sold,
            'sell_through_pct': _pct(manifest_units_sold, units),
            'remaining_units': max(units - manifest_units_sold, 0),
            'on_hand_units': on_hand.get(number, 0),
            'cogs': cogs,
            'freight_alloc': freight_alloc,
            'margin': round(bucket['net_sales'] - cogs - freight_alloc, 2),
            'recovery_pct': _pct(bucket['net_sales'], lot['landed_cost']),
            'still_to_recover': round(max(lot['landed_cost'] - bucket['net_sales'], 0.0), 2),
        })

    # A lot label that exists only on orders (no upload log, no manifest rows) is a bucket, not a pallet.
    pseudo_lots = [lot for lot in lot_rows if not lot['in_upload_logs'] and lot['manifested_units'] == 0]
    lot_rows = [lot for lot in lot_rows if lot['in_upload_logs'] or lot['manifested_units'] > 0]

    # ---- buckets & stores ------------------------------------------------
    not_on_bol = _new_money_bucket()
    inferred_total = _new_money_bucket()
    stores = defaultdict(_new_money_bucket)
    for sale in sales:
        _add_sale(stores[sale['store']], sale)
        if sale['attribution'] == 'unmatched':
            _add_sale(not_on_bol, sale)
        elif sale['attribution'] == 'inferred':
            _add_sale(inferred_total, sale)
    unmatched_returns_total = round(sum(item['amount'] for item in unmatched_returns), 2)
    for item in unmatched_returns:
        store = stores[item['store']]
        store['refunds'] = round(store['refunds'] + item['amount'], 2)
        store['net_sales'] = round(store['net_sales'] - item['amount'], 2)

    # ---- months ----------------------------------------------------------
    months = defaultdict(lambda: {
        'payouts_amazon': 0.0, 'payouts_ebay': 0.0, 'payouts_other': 0.0, 'cash_sales': 0.0,
        'lot_goods': 0.0, 'lot_freight': 0.0, 'expenses': 0.0,
        'orders': 0, 'revenue': 0.0, 'fees': 0.0, 'shipping': 0.0, 'refunds': 0.0,
    })
    for payout in payouts:
        if not payout['month']:
            continue
        key = {'amazon': 'payouts_amazon', 'ebay': 'payouts_ebay'}.get(payout['store'], 'payouts_other')
        months[payout['month']][key] = round(months[payout['month']][key] + payout['amount'], 2)
    for sale in sales:
        if not sale['month']:
            continue
        entry = months[sale['month']]
        if sale['store'] in CASH_SALE_STORES:
            entry['cash_sales'] = round(entry['cash_sales'] + sale['revenue'], 2)
        entry['orders'] += 1
        entry['revenue'] = round(entry['revenue'] + sale['revenue'], 2)
        entry['fees'] = round(entry['fees'] + sale['fees'], 2)
        entry['shipping'] = round(entry['shipping'] + sale['shipping'], 2)
        if sale['refunds']:
            refund_month = sale.get('return_month') or sale['month']
            months[refund_month]['refunds'] = round(months[refund_month]['refunds'] + sale['refunds'], 2)
    for item in unmatched_returns:
        if item['month']:
            months[item['month']]['refunds'] = round(months[item['month']]['refunds'] + item['amount'], 2)
    for lot in lot_rows:
        month = _month(lot['import_date'])
        if not month:
            continue
        months[month]['lot_goods'] = round(months[month]['lot_goods'] + (lot['goods_cost'] or 0.0), 2)
        months[month]['lot_freight'] = round(months[month]['lot_freight'] + (lot['freight'] or 0.0), 2)
    for expense in expenses:
        if expense['month']:
            months[expense['month']]['expenses'] = round(months[expense['month']]['expenses'] + expense['amount'], 2)

    month_rows = []
    cumulative = 0.0
    for month in sorted(months):
        entry = months[month]
        payouts_total = round(entry['payouts_amazon'] + entry['payouts_ebay'] + entry['payouts_other'], 2)
        cash_in = round(payouts_total + entry['cash_sales'], 2)
        cash_out = round(entry['lot_goods'] + entry['lot_freight'] + entry['expenses'], 2)
        net = round(cash_in - cash_out, 2)
        cumulative = round(cumulative + net, 2)
        net_sales = round(entry['revenue'] - entry['fees'] - entry['shipping'] - entry['refunds'], 2)
        month_rows.append({
            'month': month, 'payouts_amazon': entry['payouts_amazon'], 'payouts_ebay': entry['payouts_ebay'],
            'payouts_other': entry['payouts_other'], 'payouts': payouts_total, 'cash_sales': entry['cash_sales'],
            'cash_in': cash_in, 'lot_goods': entry['lot_goods'], 'lot_freight': entry['lot_freight'],
            'expenses': entry['expenses'], 'cash_out': cash_out, 'net': net, 'cumulative': cumulative,
            'orders': entry['orders'], 'revenue': entry['revenue'], 'fees': entry['fees'],
            'shipping': entry['shipping'], 'refunds': entry['refunds'], 'net_sales': net_sales,
        })

    def total(name):
        return round(sum(row[name] for row in month_rows), 2)

    totals = {name: total(name) for name in (
        'payouts_amazon', 'payouts_ebay', 'payouts_other', 'payouts', 'cash_sales', 'cash_in',
        'lot_goods', 'lot_freight', 'expenses', 'cash_out', 'net', 'orders', 'revenue', 'fees',
        'shipping', 'refunds', 'net_sales',
    )}
    totals['landed_cost'] = round(sum(lot['landed_cost'] for lot in lot_rows), 2)
    totals['cogs_sold'] = round(sum(lot['cogs'] + lot['freight_alloc'] for lot in lot_rows), 2)
    totals['margin_after_cogs'] = round(totals['net_sales'] - totals['cogs_sold'], 2)
    totals['stock_at_cost'] = round(max(totals['landed_cost'] - totals['cogs_sold'], 0.0), 2)
    totals['retail_on_manifests'] = round(sum(lot['retail_value'] for lot in lot_rows), 2)
    totals['not_on_bol_revenue'] = not_on_bol['revenue']

    # ---- data quality ----------------------------------------------------
    marketplace_orders = [sale for sale in sales if sale['store'] not in CASH_SALE_STORES]
    quality.update({
        'orders_counted': len(sales),
        'orders_missing_fee': sum(1 for sale in marketplace_orders if sale['fees'] == 0),
        'orders_missing_shipping': sum(1 for sale in marketplace_orders if sale['shipping'] == 0),
        'orders_no_barcode': sum(1 for sale in sales if sale['no_barcode']),
        'orders_inferred': inferred_total['orders'],
        'orders_not_on_bol': not_on_bol['orders'],
        'orders_no_cost': sum(1 for sale in sales if not sale['has_cost']),
        'revenue_no_cost': round(sum(sale['revenue'] for sale in sales if not sale['has_cost']), 2),
        'returns_unmatched': len(unmatched_returns),
        'returns_unmatched_amount': unmatched_returns_total,
        'returns_days_stale': None,
        'lots_missing_freight': [lot['lot_number'] for lot in lot_rows if lot['freight_missing']],
        'lots_missing_goods_cost': [lot['lot_number'] for lot in lot_rows if not lot['goods_cost']],
        'lots_without_manifest_rows': [lot['lot_number'] for lot in lot_rows if lot['manifested_units'] == 0],
        'lots_not_in_upload_logs': [lot['lot_number'] for lot in lot_rows if not lot['in_upload_logs']],
        'orphan_manifest_rows': orphan_rows,
        'on_hand_units_not_on_any_manifest': on_hand_unmatched,
        'price_sanity_limit': PRICE_SANITY_LIMIT,
        'payouts_first_month': min((p['month'] for p in payouts if p['month']), default=''),
        'orders_first_month': min(
            (sale['month'] for sale in sales if sale['month'] and sale['store'] not in CASH_SALE_STORES),
            default='',
        ),
    })
    if quality['returns_last_date']:
        try:
            last = dt.datetime.strptime(quality['returns_last_date'], '%Y-%m-%d')
            quality['returns_days_stale'] = max((now - last).days, 0)
        except ValueError:
            pass

    return {
        'success': True,
        'generated_at': now.strftime('%Y-%m-%d %H:%M'),
        'totals': totals,
        'months': month_rows,
        'lots': lot_rows,
        'buckets': {
            'not_on_bol': not_on_bol,
            'inferred': inferred_total,
            'pseudo_lots': [dict(lot_number=lot['lot_number'], **lot['sold']) for lot in pseudo_lots],
            'unmatched_returns': {'count': len(unmatched_returns), 'amount': unmatched_returns_total,
                                  'items': unmatched_returns[:50]},
        },
        'stores': [dict(store=name, **bucket) for name, bucket in sorted(stores.items())],
        'expenses': expenses,
        'data_quality': quality,
        'methods': [
            'Cash in = closed Amazon and eBay payouts (already net of fees, labels and refunds) plus recorded cash sales.',
            'Cash out = lot goods cost + freight (by import month) + other expenses you record here.',
            'Net sales per lot = price x quantity - seller fee - shipping label - refunds, using the sold orders table; taxes collected by the marketplace are ignored.',
            'Orders without a lot number are attributed to the lot whose manifest lists the UPC (marked inferred); a UPC that is on no manifest lands in the "not on BOL" bucket.',
            'An order whose lot is assigned but whose UPC is not on that lot\'s manifest counts as an extra (unmanifested) item of that lot.',
            'Margin after COGS = net sales - manifest cost of the units sold - freight allocated per manifested unit.',
            'A lot label that exists only on orders (no upload log, no manifest rows, e.g. "lostlot") is shown as a bucket, not as a pallet.',
        ],
    }
