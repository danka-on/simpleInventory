"""Persistent FBA exceptions and explicit, atomic return to a counting draft."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from urllib.parse import quote

from flask import jsonify, request


def now():
    return datetime.now(timezone.utc).isoformat()


def identity(item):
    code = str(item.get('barcode_key') or item.get('barcode') or '').strip().casefold()
    if code.isdigit():
        code = code.lstrip('0') or '0'
    return 'barcode:' + code if code else 'sku:' + str(item.get('seller_sku') or '').strip().casefold()


def category(reason):
    value = str(reason or '').lower()
    if any(x in value for x in ('approval', 'qualification_required', 'restricted', 'fba_inb_0021')):
        return 'approval'
    if 'brand' in value or 'catalog' in value or '8541' in value:
        return 'catalog'
    if any(x in value for x in ('missing', 'required', 'invalid', 'attribute', 'image', 'description', 'battery', 'batteries', 'dangerous')):
        return 'listing'
    return 'activation'


def locked(session):
    state = json.loads(session.get('amazon_state_json') or '{}')
    return (session.get('status') != 'open' or bool(session.get('amazon_inbound_plan_id'))
            or bool(state.get('inbound_plan_id')) or bool(state.get('boxes'))
            or bool(state.get('packing_confirmed'))
            or (session.get('amazon_stage') or 'draft') not in ('draft', ''))


class Attention:
    def __init__(self, base_dir, ensure_schema, normalize_item, approvals, context):
        self.path = base_dir / 'searchRack.db'
        self.ensure_schema = ensure_schema
        self.normalize_item = normalize_item
        self.approvals = approvals
        self.context = context

    @contextmanager
    def database(self):
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                self.ensure_schema(conn.cursor())
                conn.execute('''CREATE TABLE IF NOT EXISTS fba_attention (
                    id INTEGER PRIMARY KEY, identity_key TEXT NOT NULL UNIQUE,
                    item_json TEXT NOT NULL, source_session_id INTEGER NOT NULL,
                    source_signature TEXT NOT NULL, source_active INTEGER NOT NULL,
                    status TEXT NOT NULL, category TEXT NOT NULL,
                    original_reason TEXT NOT NULL, current_reason TEXT NOT NULL,
                    approval_required INTEGER NOT NULL DEFAULT 0,
                    checked_at TEXT, check_error TEXT NOT NULL DEFAULT '',
                    checked_sku TEXT NOT NULL DEFAULT '', assigned_session_id INTEGER,
                    revision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
                yield conn
        finally:
            conn.close()

    def sync(self, conn):
        """Latest batch owns the saved count; history is never summed as new stock."""
        sessions = [dict(r) for r in conn.execute('SELECT * FROM fba_prep_sessions ORDER BY id')]
        latest = {}
        for session in sessions:
            if session['status'] == 'deleted':
                continue
            approval = self.approvals(json.loads(session.get('amazon_state_json') or '{}'))
            for active, field in ((False, 'rejected_items_json'), (True, 'items_json')):
                for raw in json.loads(session.get(field) or '[]'):
                    item = dict(raw)
                    key = identity(item)
                    if key in ('barcode:', 'sku:') or int(item.get('quantity') or 0) <= 0:
                        continue
                    reason = approval.get(str(item.get('seller_sku') or '').casefold()) if active else None
                    reason = reason or item.get('fba_enablement_error') or item.get('inbound_error') or ''
                    failed = (not active or item.get('fba_set_aside') or
                              item.get('fba_enablement_status') == 'failed' or
                              (active and str(item.get('seller_sku') or '').casefold() in approval))
                    if active and session['status'] == 'completed':
                        failed = False
                    latest[key] = (session, item, active, bool(failed), str(reason))
        for key, (session, item, active, failed, reason) in latest.items():
            existing = conn.execute('SELECT * FROM fba_attention WHERE identity_key=?', (key,)).fetchone()
            if not failed:
                if existing and session['id'] >= existing['source_session_id'] and existing['status'] != 'assigned':
                    conn.execute("UPDATE fba_attention SET status='assigned',assigned_session_id=?,revision=revision+1,updated_at=? WHERE id=?", (session['id'], now(), existing['id']))
                continue
            reason = reason or 'FBA setup is not complete. Recheck Amazon for the current reason.'
            signature = hashlib.sha256(json.dumps([session['id'], active, item, reason], sort_keys=True).encode()).hexdigest()
            if existing and existing['source_signature'] == signature:
                continue
            # A transfer can leave the earlier rejection as useful scan history.
            if existing and existing['assigned_session_id'] and session['id'] < existing['assigned_session_id']:
                continue
            kind = category(reason)
            values = (json.dumps(item), session['id'], signature, int(active), kind, reason, reason, int(kind == 'approval'), now())
            if existing:
                conn.execute("""UPDATE fba_attention SET item_json=?,source_session_id=?,source_signature=?,source_active=?,category=?,original_reason=?,current_reason=?,approval_required=MAX(approval_required,?),updated_at=?,status='needs_attention',checked_at=NULL,check_error='',checked_sku='',assigned_session_id=NULL,revision=revision+1 WHERE id=?""", (*values, existing['id']))
            else:
                conn.execute("""INSERT INTO fba_attention(item_json,source_session_id,source_signature,source_active,category,original_reason,current_reason,approval_required,updated_at,identity_key,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,'needs_attention',?)""", (*values, key, now()))
        return sessions

    def payload(self, row):
        result = dict(row)
        item = json.loads(result.pop('item_json'))
        result['item'] = item
        result['fix_url'] = (f"/fba-prep/sessions/{result['source_session_id']}/resolve?barcode={quote(str(item.get('barcode') or ''))}"
                             if result['category'] == 'catalog' else '/listingagent?upc=' + quote(str(item.get('barcode') or '')))
        asin = item.get('asin') or (item.get('fba') or {}).get('amazon_listing', {}).get('asin') or ''
        result['approval_url'] = 'https://sellercentral.amazon.com/hz/approvalrequest/restrictions/approve?asin=' + quote(asin)
        return result

    def inspect(self, row):
        """Read Amazon only; an FNSKU never overrides a blocking listing issue."""
        from sp_api.api import ListingsItems, FbaInboundEligibility
        item = json.loads(row['item_json'])
        sku = str((item.get('proposed_fba_seller_sku') if item.get('fba_offer_strategy') == 'separate_fba_sku' else '') or item.get('seller_sku') or '').strip()
        if not sku:
            raise ValueError('No FBA seller SKU is saved. Fix the listing before rechecking.')
        credentials, seller, market, marketplace = self.context()
        response = ListingsItems(credentials=credentials, marketplace=marketplace).get_listings_item(
            seller, sku, marketplaceIds=[market], includedData=['summaries', 'issues', 'fulfillmentAvailability']).payload
        summaries = [s for s in response.get('summaries', []) if s.get('marketplaceId', market) == market]
        summary = summaries[0] if summaries else {}
        asin = item.get('asin') or (item.get('fba') or {}).get('amazon_listing', {}).get('asin')
        if asin and summary.get('asin') and asin != summary['asin']:
            return dict(status='needs_attention', category='catalog', current_reason='The saved SKU now refers to a different ASIN. Review the catalog mapping.', checked_sku=sku)
        issues = [i for i in response.get('issues', []) if str(i.get('severity') or '').upper() == 'ERROR']
        if issues:
            reason = '\n'.join(f"{i.get('code', '')}: {i.get('message', '')}" for i in issues)
            kind = category(reason + ' ' + json.dumps(issues))
            return dict(status='needs_attention', category=kind, current_reason=reason, checked_sku=sku, approval_required=int(kind == 'approval'))
        fnsku = summary.get('fnSku') or summary.get('fnsku')
        channels = [x.get('fulfillmentChannelCode') for x in response.get('fulfillmentAvailability', [])]
        if row['approval_required']:
            if not asin:
                raise ValueError('Amazon approval cannot be rechecked until an ASIN is saved.')
            preview = FbaInboundEligibility(credentials=credentials, marketplace=marketplace).get_item_eligibility_preview(
                asin=asin, marketplaceIds=[market], program='INBOUND').payload
            if preview.get('isEligibleForProgram') is not True:
                reasons = preview.get('ineligibilityReasonList') or ['Amazon has not confirmed inbound eligibility.']
                return dict(status='needs_attention', category='approval', current_reason='Amazon inbound check: ' + '; '.join(map(str, reasons)), checked_sku=sku)
        if not fnsku or not any(str(c).startswith('AMAZON') for c in channels):
            return dict(status='needs_attention', category='activation', current_reason='Waiting for Amazon to confirm an FBA offer and FNSKU. Recheck after activation finishes.', checked_sku=sku)
        item.update(seller_sku=sku, fnsku=fnsku, amazon_fnsku=fnsku)
        return dict(status='ready_to_retry', category='ready', current_reason='FBA offer is active and no blocking listing errors were returned. Amazon will validate the next inbound plan.', checked_sku=sku, item_json=json.dumps(item))

    def recheck(self, item_id):
        with self.database() as conn:
            self.sync(conn)
            row = conn.execute('SELECT * FROM fba_attention WHERE id=?', (item_id,)).fetchone()
            if not row:
                raise ValueError('Attention item not found.')
            row = dict(row)
        if row['status'] == 'assigned':
            raise ValueError('This item is already in a shipment. Open that shipment to continue.')
        try:
            change = self.inspect(row)
            change.update(checked_at=now(), check_error='', updated_at=now())
        except Exception as exc:
            # Keep the real rejection, not the network error, as the item reason.
            detail = str(exc) if isinstance(exc, ValueError) else 'Amazon could not be checked. Try again shortly or open Seller Central.'
            change = dict(status='needs_attention', checked_at=now(), check_error=detail, updated_at=now())
            if row['status'] == 'ready_to_retry':
                change.update(category=category(row['original_reason']), current_reason=row['original_reason'])
        with self.database() as conn:
            self.sync(conn)
            if 'approval_required' in change:
                change['approval_required'] = max(row['approval_required'], change['approval_required'])
            result = conn.execute('UPDATE fba_attention SET ' + ','.join(k + '=?' for k in change) + ',revision=revision+1 WHERE id=? AND revision=?', (*change.values(), item_id, row['revision']))
            if result.rowcount != 1:
                raise ValueError('This saved item changed during the check. Refresh and recheck it.')
            return self.payload(conn.execute('SELECT * FROM fba_attention WHERE id=?', (item_id,)).fetchone())

    def add(self, item_id, destination):
        checked = self.recheck(item_id)
        if checked['status'] != 'ready_to_retry':
            raise ValueError(checked['check_error'] or checked['current_reason'])
        with self.database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            sessions = self.sync(conn)
            row = conn.execute('SELECT * FROM fba_attention WHERE id=?', (item_id,)).fetchone()
            if row['revision'] != checked['revision'] or row['status'] != 'ready_to_retry':
                raise ValueError('Item changed or was already added. Refresh the list.')
            item = json.loads(row['item_json'])
            for session in sessions:
                if session['status'] != 'open':
                    continue
                if any(identity(i) == row['identity_key'] or (item.get('seller_sku') and str(i.get('seller_sku') or '').casefold() == item['seller_sku'].casefold()) for i in json.loads(session['items_json'] or '[]')):
                    raise ValueError(f"This item is still in {session['session_name']}. Set it aside there before moving it to another shipment.")
            target = next((s for s in sessions if s['id'] == destination), None) if destination else None
            if destination and (not target or locked(target)):
                raise ValueError('Choose a counting draft without an Amazon plan or packed boxes.')
            if target and conn.execute('SELECT 1 FROM fba_pack_scans WHERE session_id=? LIMIT 1', (destination,)).fetchone():
                raise ValueError('This shipment has packing scans and cannot receive retry items.')
            timestamp = now()
            if not target:
                destination = conn.execute("INSERT INTO fba_prep_sessions(session_name,items_json,item_count,total_units,status,created_at,updated_at) VALUES(?,'[]',0,0,'open',?,?)", ('FBA retry ' + timestamp[:16].replace('T', ' '), timestamp, timestamp)).lastrowid
                target = dict(items_json='[]')
            item.update(fba_set_aside=False, fba_enablement_status='ready', fba_enablement_error='', inbound_error='', fba_enablement_checked_at=timestamp, box_number='')
            item = self.normalize_item(item)
            items = json.loads(target['items_json'] or '[]') + [item]
            conn.execute('UPDATE fba_prep_sessions SET items_json=?,item_count=?,total_units=?,count_revision=count_revision+1,updated_at=? WHERE id=?', (json.dumps(items), len(items), sum(int(i.get('quantity') or 0) for i in items), timestamp, destination))
            conn.execute("UPDATE fba_attention SET status='assigned',assigned_session_id=?,revision=revision+1,updated_at=? WHERE id=?", (destination, timestamp, item_id))
            return destination


def register(app, base_dir, ensure_schema, normalize_item, approvals, context):
    service = Attention(base_dir, ensure_schema, normalize_item, approvals, context)

    @app.get('/api/fba-prep/attention')
    def fba_attention_list():
        with service.database() as conn:
            sessions = service.sync(conn)
            rows = [service.payload(r) for r in conn.execute('SELECT * FROM fba_attention ORDER BY updated_at DESC,id DESC')]
        return jsonify(success=True, items=rows, drafts=[dict(id=s['id'], name=s['session_name']) for s in sessions if not locked(s)])

    @app.post('/api/fba-prep/attention/<int:item_id>/<action>')
    def fba_attention_action(item_id, action):
        try:
            if action == 'recheck':
                return jsonify(success=True, item=service.recheck(item_id))
            if action == 'add':
                data = request.get_json(silent=True) or {}
                destination = int(data.get('session_id') or 0)
                return jsonify(success=True, session_id=service.add(item_id, destination))
            return jsonify(success=False, error='Unknown attention action.'), 400
        except (ValueError, TypeError) as exc:
            return jsonify(success=False, error=str(exc)), 409
        except Exception:
            app.logger.exception('FBA attention action failed')
            return jsonify(success=False, error='Could not save this change. Refresh and try again.'), 500

    return service
