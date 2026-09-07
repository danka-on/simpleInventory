"""Listing log for Sweet Shelves."""

import json
from flask import jsonify, render_template, request
from . import (
    database as ss_database, errors as ss_errors, listing_checks as ss_listing_checks, listing_settings
    as ss_listing_settings,
)


def _listinglog_init_tables(cur):
    # listinglog.db might be created after startup; ensure WAL gets enabled.
    try:
        cur.execute('PRAGMA journal_mode=WAL')
    except Exception:
        pass

    cur.execute('''
        CREATE TABLE IF NOT EXISTS listing_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            upc TEXT NOT NULL,
            platform TEXT NOT NULL,
            action TEXT NOT NULL,
            source TEXT,
            marketplace_id TEXT,
            listing_id TEXT,
            offer_id TEXT,
            sku TEXT,
            asin TEXT,
            url TEXT,
            title TEXT,
            price REAL,
            quantity INTEGER,
            success INTEGER NOT NULL DEFAULT 1,
            error TEXT,
            meta_json TEXT
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_log_upc_created_at ON listing_log(upc, created_at, id)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_log_platform_created_at ON listing_log(platform, created_at, id)')


def _listinglog_add_entry(*,
                          upc,
                          platform,
                          action='listed',
                          source='listingagent',
                          created_at=None,
                          marketplace_id=None,
                          listing_id=None,
                          offer_id=None,
                          sku=None,
                          asin=None,
                          url=None,
                          title=None,
                          price=None,
                          quantity=None,
                          success=True,
                          error=None,
                          meta=None):
    upc = (upc or '').strip()
    if not upc:
        raise ValueError('upc is required')

    platform = (platform or 'unknown').strip().lower() or 'unknown'
    action = (action or 'listed').strip().lower() or 'listed'
    source = (source or '').strip() or None
    created_at = (created_at or ss_listing_checks._listagent_now_iso()).strip()

    meta_json = None
    try:
        if meta is not None:
            meta_json = json.dumps(meta, ensure_ascii=False)
    except Exception:
        meta_json = None

    with ss_database.db_connection('listinglog.db') as conn:
        cur = conn.cursor()
        _listinglog_init_tables(cur)
        cur.execute('''
            INSERT INTO listing_log (
                created_at, upc, platform, action, source, marketplace_id,
                listing_id, offer_id, sku, asin, url, title, price, quantity,
                success, error, meta_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            created_at, upc, platform, action, source, (marketplace_id or None),
            (listing_id or None), (offer_id or None), (sku or None), (asin or None),
            (url or None), (title or None), price, quantity,
            1 if success else 0, (error or None), meta_json
        ))
        rid = cur.lastrowid
        cur.execute('SELECT * FROM listing_log WHERE id = ? LIMIT 1', (rid,))
        return ss_listing_checks._listagent_row_to_dict(cur.fetchone())


def page_listing_log():
    """Dedicated Listing Log page."""
    return render_template('listing_log.html')


def api_listinglog_recent():
    """Recent listing events (listinglog.db)."""
    try:
        upc = (request.args.get('upc') or '').strip()
        platform = (request.args.get('platform') or '').strip().lower()
        limit = ss_listing_settings._listingagent_parse_int(request.args.get('limit'), 200) or 200
        limit = max(1, min(limit, 1000))

        with ss_database.db_connection('listinglog.db') as conn:
            cur = conn.cursor()
            _listinglog_init_tables(cur)

            sql = 'SELECT * FROM listing_log WHERE 1=1'
            params = []
            if upc:
                sql += ' AND upc = ? COLLATE NOCASE'
                params.append(upc)
            if platform:
                sql += ' AND platform = ?'
                params.append(platform)
            sql += ' ORDER BY created_at DESC, id DESC LIMIT ?'
            params.append(limit)

            cur.execute(sql, params)
            rows = [ss_listing_checks._listagent_row_to_dict(r) for r in cur.fetchall()]

        return jsonify({'success': True, 'items': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listinglog:recent')}), 500
