"""Bol inventory for Sweet Shelves."""

import os
import sqlite3
from flask import jsonify, request, session
from . import (
    caching as ss_caching, database as ss_database, errors as ss_errors, facebook as ss_facebook,
    listing_lifecycle as ss_listing_lifecycle, listing_log as ss_listing_log, listing_queue as
    ss_listing_queue, normalization as ss_normalization, prep_media as ss_prep_media, prep_schema as
    ss_prep_schema, runtime as ss_runtime, warehouse_allocations as ss_warehouse_allocations,
    warehouse_matching as ss_warehouse_matching,
)


# NO CACHE - Items-to-list needs fresh data for marketplace checkboxes
def api_bol_items():
    """Return BOL items with sorting and filters: lot (exact), import_date (exact), sort by date/name/qty."""
    conn = None
    try:
        debug_items_to_list = (os.getenv('ITEMS_TO_LIST_DEBUG') or '').strip() == '1'
        ss_listing_lifecycle._ensure_bol_list_status_column()
        ss_database._ensure_bol_items_fast_indexes()
        sort = request.args.get('sort', 'date_desc')
        lot = (request.args.get('lot') or '').strip()
        lot_filter_key = lot.lower()
        lot_filter_is_all = (not lot) or lot_filter_key in ('all', 'all lots')
        lot_filter_is_lotless = lot_filter_key == 'lotless'
        lot_filter_is_returns = lot_filter_key == 'returns'
        group_multi_lot_rows = lot_filter_is_all
        import_date = (request.args.get('import_date') or '').strip()
        q = (request.args.get('q') or '').strip()
        # Strip leading zeros from numeric barcode searches
        q_stripped = ss_normalization._strip_leading_zeros_numeric(q)
        direct_search = str(request.args.get('direct_search') or '').strip().lower() in ('1', 'true', 'yes', 'y', 'on')
        status_filter = (request.args.get('status') or '').strip().lower()
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 25))
        if page < 1: page = 1
        # Allow large limits for stats calculation (up to 50000), otherwise cap at 100
        if limit < 1: limit = 25
        elif limit > 50000: limit = 50000
        elif limit <= 100: pass  # Normal range
        # else: allow limits between 100 and 50000 for stats
        offset = (page - 1) * limit
        
        # Parse comma-separated status filters (e.g., "good,bad").
        # /items-to-list only surfaces actionable rows now, so unchecked is ignored.
        status_filters = [s.strip() for s in status_filter.split(',') if s.strip()] if status_filter else []
        allowed_status_filters = ('good', 'bad', 'return')
        normalized_status_filters = []
        for raw_status in status_filters:
            normalized = raw_status.replace(' ', '_').strip().lower()
            if normalized in allowed_status_filters and normalized not in normalized_status_filters:
                normalized_status_filters.append(normalized)
        status_filters = normalized_status_filters
        
        # Get listed/not_listed filters
        listed_flag = request.args.get('listed', '').strip().lower() == 'true'
        not_listed_flag = request.args.get('not_listed', '').strip().lower() == 'true'
        warehouse_filter = (request.args.get('warehouse_filter') or '').strip().lower()
        if warehouse_filter == 'multi_locations':
            warehouse_filter = 'multiple_locations'
        if warehouse_filter not in ('', 'with_quantity', 'without_quantity', 'multiple_locations'):
            warehouse_filter = ''
        needs_enriched_post_filter = bool(warehouse_filter)
        
        # Get defect filter
        defect_filter = (request.args.get('defect') or '').strip()

        if direct_search and q_stripped:
            lot = ''
            lot_filter_key = ''
            lot_filter_is_all = True
            lot_filter_is_lotless = False
            lot_filter_is_returns = False
            group_multi_lot_rows = True
            status_filters = []
            listed_flag = False
            not_listed_flag = False
            warehouse_filter = ''
            needs_enriched_post_filter = False
            defect_filter = ''

        if debug_items_to_list:
            print(f"[api_bol_items] Request: page={page}, limit={limit}, q={q_stripped}, direct_search={direct_search}, status={status_filters}, defect={defect_filter}, _v={request.args.get('_v')}, _t={request.args.get('_t')}")
            print(
                f"[api_bol_items] Filters - lot: '{lot}', import_date: '{import_date}', q: '{q_stripped}', "
                f"status_filters: {status_filters}, listed: {listed_flag}, not_listed: {not_listed_flag}, "
                f"warehouse_filter: '{warehouse_filter}'"
            )
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bol_items'")
        if not cur.fetchone():
            return jsonify({'results': []})
        # Ensure prep tables exist for left join and statuses
        ss_prep_schema._ensure_items_prep_tables()
        cur.execute("PRAGMA table_info(bol_items)")
        cols = [r[1] for r in cur.fetchall()]
        has_temporary = any(c.lower() == 'temporary' for c in cols)
        has_itemprepped = any(c.lower() == 'itemprepped' for c in cols)
        status_filter_has_good_qty = any(c.lower() == 'good_qty' for c in cols)
        status_filter_has_bad_qty = any(c.lower() == 'bad_qty' for c in cols)
        
        # Optional diagnostics for schema mismatches.
        marketplace_cols = [c for c in cols if 'listed' in c.lower()]
        if debug_items_to_list and not marketplace_cols:
            print(f"[api_bol_items] WARNING: No 'listed' columns found in bol_items table!")
            print(f"[api_bol_items] Available columns: {cols}")
        
        where = []
        params = []
        prep_status_expr = "COALESCE(s_exact.status, s_fallback.status)"
        prep_reason_expr = "COALESCE(s_exact.reason, s_fallback.reason)"
        prep_note_expr = "COALESCE(s_exact.note, s_fallback.note)"
        prep_updated_expr = "COALESCE(s_exact.updated_at, s_fallback.updated_at)"
        prep_qty_expr = "COALESCE(s_exact.quantity, s_fallback.quantity)"
        prep_lot_expr = "COALESCE(s_exact.lot_number, s_fallback.lot_number, '')"
        prep_status_value_expr = f"LOWER(TRIM(COALESCE({prep_status_expr}, '')))"
        suffixed_upc_expr = "INSTR(COALESCE(b.upc, ''), '-') > 0"
        explicit_suffixed_special_status_expr = f"({prep_status_value_expr} IN ('bad', 'return') AND {suffixed_upc_expr})"
        invalid_base_special_status_expr = f"({prep_status_value_expr} IN ('bad', 'return') AND NOT ({suffixed_upc_expr}))"
        fallback_source_expr = f"({prep_status_value_expr} = '' OR {invalid_base_special_status_expr})"
        fallback_good_expr = (
            f"({fallback_source_expr} AND COALESCE(b.good_qty, 0) > 0 "
            f"AND (NOT ({suffixed_upc_expr}) OR COALESCE(b.bad_qty, 0) <= 0))"
        )
        fallback_bad_expr = (
            f"({fallback_source_expr} AND {suffixed_upc_expr} AND COALESCE(b.bad_qty, 0) > 0)"
        )
        # Canonicalize duplicate corruption rows (same UPC+LOT) to newest row only.
        # MAX(id) is much faster than per-row ORDER BY and is backed by idx_bol_items_upc_lotnorm_id.
        where.append('''
            b.id = (
                SELECT MAX(b2.id)
                FROM bol_items b2
                WHERE b2.upc = b.upc COLLATE NOCASE
                  AND COALESCE(b2.lot_number, '') = COALESCE(b.lot_number, '')
            )
        ''')
        # Exclude itemprepped entries (internal prep tracking) from item manager
        if has_itemprepped:
            where.append('(b.itemprepped IS NULL OR b.itemprepped = 0)')
        # Treat 'all'/'all lots' as no filter and LOTLESS as blank lot rows.
        if lot and not lot_filter_is_all:
            if lot_filter_is_lotless:
                where.append("TRIM(COALESCE(b.lot_number, '')) = ''")
                where.append(f"NOT ({prep_status_value_expr} = 'return' AND {suffixed_upc_expr})")
            elif lot_filter_is_returns:
                where.append(f"({prep_status_value_expr} = 'return' AND {suffixed_upc_expr})")
            else:
                where.append('b.lot_number = ?')
                params.append(lot)
        if import_date:
            where.append('b.import_date = ?')
            params.append(import_date)
        if q_stripped:
            q_variants = ss_listing_queue._items_to_list_upc_search_variants(q)
            q_clauses = ['b.upc LIKE ? COLLATE NOCASE', 'b.item_description LIKE ? COLLATE NOCASE']
            like = f"%{q_stripped}%"
            params.extend([like, like])
            if q_variants:
                placeholders = ','.join('?' for _ in q_variants)
                q_clauses.append(f"LOWER(TRIM(b.upc)) IN ({placeholders})")
                params.extend([str(v).strip().lower() for v in q_variants])
            where.append(f"({' OR '.join(q_clauses)})")

        # /items-to-list should only show rows that were actually prepped into a visible status.
        if status_filter_has_good_qty and status_filter_has_bad_qty:
            visible_status_clause = (
                f"(({prep_status_value_expr} = 'good') "
                f"OR {explicit_suffixed_special_status_expr} "
                f"OR {fallback_good_expr} "
                f"OR {fallback_bad_expr})"
            )
        else:
            visible_status_clause = (
                f"(({prep_status_value_expr} = 'good') "
                f"OR {explicit_suffixed_special_status_expr})"
            )
        where.append(visible_status_clause)
        
        # Apply status filter in SQL WHERE clause (supports multiple statuses)
        if status_filters:
            status_conditions = []
            for sf in status_filters:
                if sf == 'good':
                    # Good rows can still exist with missing status metadata after complex undo/delete history.
                    if status_filter_has_good_qty:
                        status_conditions.append(f"({prep_status_value_expr} = 'good' OR {fallback_good_expr})")
                    else:
                        status_conditions.append(f"{prep_status_value_expr} = 'good'")
                elif sf == 'bad':
                    if status_filter_has_bad_qty:
                        status_conditions.append(f"(({prep_status_value_expr} = 'bad' AND {suffixed_upc_expr}) OR {fallback_bad_expr})")
                    else:
                        status_conditions.append(f"({prep_status_value_expr} = 'bad' AND {suffixed_upc_expr})")
                elif sf == 'return':
                    status_conditions.append(f"({prep_status_value_expr} = 'return' AND {suffixed_upc_expr})")
            
            if status_conditions:
                where.append(f"({' OR '.join(status_conditions)})")
        
        # Apply listed/not_listed filter (optionally by marketplace)
        listed_stores = [s.strip().lower() for s in (request.args.get('listed_stores') or '').split(',') if s.strip()]
        # Default to all stores if none specified
        if not listed_stores:
            listed_stores = ['amazon', 'ebay', 'facebook']

        if listed_flag:
            if listed_stores:
                store_clauses = [f"COALESCE(b.listed_{s}, 0) = 1" for s in listed_stores]
                where.append(f"({' OR '.join(store_clauses)})")
            else:
                where.append("(b.list_status = 'listed')")
        elif not_listed_flag:
            if listed_stores:
                store_clauses = [f"COALESCE(b.listed_{s}, 0) = 0" for s in listed_stores]
                where.append(f"({' AND '.join(store_clauses)})")
            else:
                where.append("(b.list_status IS NULL OR b.list_status = '' OR b.list_status != 'listed')")
        
        # Apply defect filter (filter by prep_reason column)
        if defect_filter:
            where.append(f"{prep_reason_expr} LIKE ? COLLATE NOCASE")
            params.append(f"%{defect_filter}%")
        
        # Build WHERE clause only when we actually have conditions
        where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
        prep_join = (
            " FROM bol_items b "
            "LEFT JOIN items_prep_status s_exact "
            "ON s_exact.upc = b.upc "
            "AND COALESCE(s_exact.lot_number, '') = COALESCE(b.lot_number, '') "
            "LEFT JOIN items_prep_status s_fallback "
            "ON s_fallback.upc = b.upc "
            "AND COALESCE(s_fallback.lot_number, '') = '' "
            "AND s_exact.id IS NULL "
        )
        # Build sort
        order_sql = ' ORDER BY '
        # Support sorting by last_edited (prep_updated_at if present, else import_date)
        if sort == 'date_asc':
            order_sql += "b.import_date ASC, b.id ASC"
        elif sort == 'name':
            order_sql += "b.item_description COLLATE NOCASE ASC"
        elif sort == 'last_edited':
            # newest last_edited first
            order_sql += f"COALESCE({prep_updated_expr}, b.import_date) DESC, b.id DESC"
        elif sort == 'last_edited_asc':
            order_sql += f"COALESCE({prep_updated_expr}, b.import_date) ASC, b.id ASC"
        else:
            # date_desc default
            order_sql += "b.import_date DESC, b.id DESC"
        # Get total count and total quantity (SQL-level filters only).
        # unique_items == total because rows are canonicalized to one row per UPC+LOT above.
        count_sql = "SELECT COUNT(*), SUM(COALESCE(b.quantity, 1))" + prep_join + where_sql
        query_params = list(params)
        if not needs_enriched_post_filter and not group_multi_lot_rows:
            cur.execute(count_sql, tuple(query_params))
            count_row = cur.fetchone()
            total = count_row[0]
            unique_items = total
            total_quantity = count_row[1] or 0
        else:
            total = 0
            unique_items = 0
            total_quantity = 0
        
        # Check if new quantity columns exist
        has_original_qty = any(c.lower() == 'original_qty' for c in cols)
        has_good_qty = any(c.lower() == 'good_qty' for c in cols)
        has_bad_qty = any(c.lower() == 'bad_qty' for c in cols)
        has_unchecked_qty = any(c.lower() == 'unchecked_qty' for c in cols)
        has_listed_amazon = any(c.lower() == 'listed_amazon' for c in cols)
        has_listed_ebay = any(c.lower() == 'listed_ebay' for c in cols)
        has_listed_facebook = any(c.lower() == 'listed_facebook' for c in cols)
        has_listed_amazon_source = any(c.lower() == 'listed_amazon_source' for c in cols)
        has_listed_ebay_source = any(c.lower() == 'listed_ebay_source' for c in cols)
        has_listed_facebook_source = any(c.lower() == 'listed_facebook_source' for c in cols)
        
        if debug_items_to_list:
            print(f"[api_bol_items] Marketplace column detection:")
            print(f"  has_listed_amazon: {has_listed_amazon}")
            print(f"  has_listed_ebay: {has_listed_ebay}")
            print(f"  has_listed_facebook: {has_listed_facebook}")
            print(f"  Actual columns: {[c for c in cols if 'listed' in c.lower()]}")
        
        sql = (
            'SELECT b.id, b.upc, b.item_description, b.image_url, b.lot_number, b.bol_number, b.import_date, b.list_status, b.quantity, ' +
            ('b.temporary, ' if has_temporary else '') +
            ('b.original_qty, ' if has_original_qty else '') +
            ('b.good_qty, ' if has_good_qty else '') +
            ('b.bad_qty, ' if has_bad_qty else '') +
            ('b.unchecked_qty, ' if has_unchecked_qty else '') +
            ('b.listed_amazon, b.listed_amazon_date, ' if has_listed_amazon else '') +
            ('b.listed_amazon_source, ' if has_listed_amazon_source else '') +
            ('b.listed_ebay, b.listed_ebay_date, ' if has_listed_ebay else '') +
            ('b.listed_ebay_source, ' if has_listed_ebay_source else '') +
            ('b.listed_facebook, b.listed_facebook_date, ' if has_listed_facebook else '') +
            ('b.listed_facebook_source, ' if has_listed_facebook_source else '') +
            f"{prep_status_expr} as prep_status, {prep_reason_expr} as prep_reason, {prep_note_expr} as prep_note, {prep_updated_expr} as prep_updated_at, "
            f"{prep_qty_expr} as prep_quantity, {prep_lot_expr} as prep_lot_number "
            + prep_join
            + where_sql + order_sql
        )
        if needs_enriched_post_filter or group_multi_lot_rows:
            # Warehouse filters require full candidate set before pagination.
            cur.execute(sql, tuple(query_params))
        else:
            paged_params = list(query_params)
            paged_params.extend([limit, offset])
            cur.execute(sql + ' LIMIT ? OFFSET ?', tuple(paged_params))
        rows = [dict(r) for r in cur.fetchall()]
        
        if debug_items_to_list and rows and len(rows) > 0:
            first_row = rows[0]
            print(f"[api_bol_items] First row keys: {list(first_row.keys())}")
            print(f"[api_bol_items] First row marketplace data: listed_amazon={first_row.get('listed_amazon')}, listed_ebay={first_row.get('listed_ebay')}, listed_facebook={first_row.get('listed_facebook')}")

        # Warehouse availability map by normalized base UPC from searchRack.db.
        warehouse_available_by_base = {}
        warehouse_locations_by_base = {}
        warehouse_location_qty_by_base = {}
        warehouse_exact_by_upc = {}  # qty keyed by exact normalized UPC (suffix preserved)

        def _warehouse_base_upc(value):
            upc_norm = ss_normalization._normalize_upc(value)
            if not upc_norm:
                return ''
            base = upc_norm.split('-', 1)[0].strip()
            return ss_normalization._strip_leading_zeros_numeric(base)

        try:
            page_base_upcs = set()
            for r in rows:
                upc_base = _warehouse_base_upc(r.get('upc'))
                if upc_base:
                    page_base_upcs.add(upc_base)

            if page_base_upcs:
                with sqlite3.connect('searchRack.db') as sr_conn:
                    sr_conn.row_factory = sqlite3.Row
                    sr_cur = sr_conn.cursor()

                    sr_cur.execute('PRAGMA table_info(SEARCHRACK)')
                    sr_cols = [c[1] for c in sr_cur.fetchall()]
                    sr_cols_lower = {c.lower(): c for c in sr_cols}
                    barcode_col = sr_cols_lower.get('barcode') or sr_cols_lower.get('upc')
                    qty_col = sr_cols_lower.get('quantity') or sr_cols_lower.get('qty')
                    loc_col = sr_cols_lower.get('item_position') or sr_cols_lower.get('itemposition') or sr_cols_lower.get('position')
                    pic_col = sr_cols_lower.get('pictureposition')

                    if barcode_col and qty_col:
                        loc_expr = f"{loc_col} AS LOC" if loc_col else "NULL AS LOC"
                        pic_expr = f"{pic_col} AS PIC" if pic_col else "NULL AS PIC"
                        id_col = sr_cols_lower.get('id')
                        id_expr = id_col if id_col else 'rowid'
                        select_prefix = (
                            f"SELECT {id_expr} AS RID, {barcode_col} AS BARCODE, {qty_col} AS QTY, {loc_expr}, {pic_expr} "
                            f"FROM SEARCHRACK WHERE {barcode_col} IS NOT NULL AND {barcode_col} != '' AND "
                        )

                        seen_rids = set()

                        def _consume_warehouse_rows(sr_rows):
                            for sr_row in sr_rows:
                                rid = sr_row['RID']
                                if rid in seen_rids:
                                    continue
                                seen_rids.add(rid)
                                sr_base = _warehouse_base_upc(sr_row['BARCODE'])
                                if not sr_base or sr_base not in page_base_upcs:
                                    continue
                                try:
                                    qty_val = int(float(sr_row['QTY'])) if sr_row['QTY'] is not None else 0
                                except Exception:
                                    qty_val = 0
                                warehouse_available_by_base[sr_base] = warehouse_available_by_base.get(sr_base, 0) + qty_val
                                sr_exact = ss_normalization._normalize_upc_preserve_suffix_for_match(str(sr_row['BARCODE'] or '').strip())
                                if sr_exact:
                                    warehouse_exact_by_upc[sr_exact] = warehouse_exact_by_upc.get(sr_exact, 0) + qty_val

                                location_label = ((sr_row['LOC'] or '').strip() or (sr_row['PIC'] or '').strip())
                                if location_label:
                                    loc_list = warehouse_locations_by_base.get(sr_base)
                                    if loc_list is None:
                                        loc_list = []
                                        warehouse_locations_by_base[sr_base] = loc_list
                                    if location_label not in loc_list:
                                        loc_list.append(location_label)
                                    loc_qty_map = warehouse_location_qty_by_base.get(sr_base)
                                    if loc_qty_map is None:
                                        loc_qty_map = {}
                                        warehouse_location_qty_by_base[sr_base] = loc_qty_map
                                    loc_qty_map[location_label] = loc_qty_map.get(location_label, 0) + qty_val

                        # Fast path: targeted barcode/prefix lookup for normal paged requests.
                        # Fallback: full scan only when candidate set is very large.
                        if len(page_base_upcs) <= 400:
                            candidate_exact = set()
                            candidate_prefix = set()
                            for base_upc in page_base_upcs:
                                for cand in ss_warehouse_matching._sold_removal_barcode_variants(base_upc):
                                    c = str(cand or '').strip()
                                    if not c:
                                        continue
                                    candidate_exact.add(c)
                                    candidate_prefix.add(f"{c}-%")

                            for part in ss_listing_lifecycle._chunk_list(sorted(candidate_exact), 700):
                                placeholders = ','.join('?' for _ in part)
                                sr_cur.execute(select_prefix + f"{barcode_col} IN ({placeholders})", tuple(part))
                                _consume_warehouse_rows(sr_cur.fetchall())

                            for part in ss_listing_lifecycle._chunk_list(sorted(candidate_prefix), 120):
                                like_sql = ' OR '.join(f"{barcode_col} LIKE ?" for _ in part)
                                sr_cur.execute(select_prefix + f"({like_sql})", tuple(part))
                                _consume_warehouse_rows(sr_cur.fetchall())
                        else:
                            sr_cur.execute(select_prefix + "1=1")
                            _consume_warehouse_rows(sr_cur.fetchall())
                    else:
                        print("Error reading searchRack warehouse availability: missing BARCODE/UPC or QUANTITY/QTY columns")

            for upc_base, total_qty in list(warehouse_available_by_base.items()):
                warehouse_available_by_base[upc_base] = max(0, int(total_qty or 0))
            for upc_base, loc_qty_map in list(warehouse_location_qty_by_base.items()):
                cleaned = {}
                if isinstance(loc_qty_map, dict):
                    for raw_loc, raw_qty in loc_qty_map.items():
                        loc = str(raw_loc or '').strip()
                        if not loc:
                            continue
                        try:
                            qty = int(raw_qty)
                        except Exception:
                            qty = 0
                        cleaned[loc] = max(0, cleaned.get(loc, 0) + qty)
                warehouse_location_qty_by_base[upc_base] = cleaned
        except Exception as e:
            print(f"Error reading warehouse availability from searchRack: {e}")
            warehouse_available_by_base = {}
            warehouse_locations_by_base = {}
            warehouse_location_qty_by_base = {}
            warehouse_exact_by_upc = {}
        
        # Build a status map keyed by (upc, lot_number) with fallback to lotless legacy rows.
        status_map = {}
        note_map = {}
        set_note_map = {}
        photo_count_map = {}
        audio_count_map = {}
        video_count_map = {}
        upc_lot_count_map = {}
        bol_lot_rows_by_upc = {}
        page_upcs = set()
        for r in rows:
            u = r.get('upc')
            if u:
                page_upcs.add(str(u))
                page_upcs.add(ss_normalization._normalize_upc(u))
                # Return/bad rows use a suffixed UPC, while their original BOL
                # LOT history remains attached to the base barcode.
                upc_with_suffix = ss_normalization._normalize_upc(u)
                if ss_normalization._is_items_to_list_suffixed_upc(upc_with_suffix):
                    base_upc = upc_with_suffix.rsplit('-', 1)[0].strip()
                    if base_upc:
                        page_upcs.add(base_upc)
                        page_upcs.add(ss_normalization._strip_leading_zeros_numeric(base_upc))
        try:
            if page_upcs:
                ss_prep_schema._ensure_items_prep_tables()
                from DBmanager import connect_db
                with connect_db('bol.db') as c2:
                    c2.row_factory = sqlite3.Row
                    k2 = c2.cursor()
                    placeholders = ','.join('?' for _ in page_upcs)
                    lot_original_expr = ('COALESCE(original_qty, 0)' if has_original_qty else 'NULL')
                    lot_good_expr = ('COALESCE(good_qty, 0)' if has_good_qty else '0')
                    lot_bad_expr = ('COALESCE(bad_qty, 0)' if has_bad_qty else '0')
                    lot_unchecked_expr = ('COALESCE(unchecked_qty, 0)' if has_unchecked_qty else 'NULL')
                    k2.execute(f'''
                        SELECT
                            b.upc,
                            COALESCE(b.lot_number, '') as lot_number,
                            COALESCE(b.import_date, '') as import_date,
                            COALESCE(b.quantity, 1) as bol_quantity,
                            {lot_original_expr} as original_qty,
                            {lot_good_expr} as good_qty,
                            {lot_bad_expr} as bad_qty,
                            {lot_unchecked_expr} as unchecked_qty
                        FROM bol_items b
                        WHERE b.upc IN ({placeholders})
                          AND b.id = (
                              SELECT MAX(b2.id)
                              FROM bol_items b2
                              WHERE b2.upc = b.upc COLLATE NOCASE
                                AND COALESCE(b2.lot_number, '') = COALESCE(b.lot_number, '')
                          )
                        ORDER BY COALESCE(b.import_date, '') DESC, b.id DESC
                    ''', tuple(page_upcs))
                    for rr in k2.fetchall():
                        raw_upc = rr['upc']
                        if not raw_upc:
                            continue
                        upc_key = ss_normalization._normalize_upc(raw_upc)
                        if not upc_key:
                            continue
                        bol_lot_rows_by_upc.setdefault(upc_key, []).append({
                            'upc': raw_upc,
                            'lot_number': ss_normalization._normalize_lot_number(rr['lot_number']),
                            'import_date': rr['import_date'] or '',
                            'bol_quantity': rr['bol_quantity'],
                            'original_qty': rr['original_qty'],
                            'good_qty': rr['good_qty'],
                            'bad_qty': rr['bad_qty'],
                            'unchecked_qty': rr['unchecked_qty']
                        })
                    for upc_key, lot_rows in bol_lot_rows_by_upc.items():
                        upc_lot_count_map[upc_key] = len({
                            ss_normalization._normalize_lot_number(row.get('lot_number'))
                            for row in lot_rows
                        })

                    k2.execute(f'''
                        SELECT upc, COALESCE(lot_number, '') as lot_number, status, reason, note, quantity, updated_at
                        FROM items_prep_status
                        WHERE upc IN ({placeholders})
                    ''', tuple(page_upcs))
                    for rr in k2.fetchall():
                        raw_upc = rr['upc']
                        if not raw_upc:
                            continue
                        key = (ss_normalization._normalize_upc(raw_upc), ss_normalization._normalize_lot_number(rr['lot_number']))
                        status_map[key] = {
                            'prep_status': rr['status'],
                            'prep_reason': rr['reason'],
                            'prep_note': rr['note'],
                            'prep_quantity': rr['quantity'],
                            'prep_updated_at': rr['updated_at']
                        }

                    # Notes are still stored per UPC; apply as a shared note flag.
                    k2.execute(f'''
                        SELECT
                            upc,
                            COALESCE(row_status, '') as row_status,
                            COUNT(*) as note_count,
                            MAX(CASE WHEN INSTR(COALESCE(note, ''), 'Set of ') > 0 THEN 1 ELSE 0 END) as set_note_flag
                        FROM items_prep_notes
                        WHERE upc IN ({placeholders})
                          AND TRIM(COALESCE(note, '')) != ''
                        GROUP BY upc, COALESCE(row_status, '')
                    ''', tuple(page_upcs))
                    for rr in k2.fetchall():
                        raw_upc = rr['upc']
                        if raw_upc:
                            scope_key = ss_normalization._items_to_list_asset_scope(raw_upc, rr['row_status'])
                            note_map[scope_key] = int(rr['note_count'] or 0)
                            set_note_map[scope_key] = int(rr['set_note_flag'] or 0)

                    k2.execute(f'''
                        SELECT
                            upc,
                            COALESCE(row_status, '') as row_status,
                            COUNT(*) as audio_count
                        FROM items_prep_media
                        WHERE upc IN ({placeholders})
                          AND COALESCE(media_type, '') = 'audio'
                        GROUP BY upc, COALESCE(row_status, '')
                    ''', tuple(page_upcs))
                    for rr in k2.fetchall():
                        raw_upc = rr['upc']
                        if raw_upc:
                            scope_key = ss_normalization._items_to_list_asset_scope(raw_upc, rr['row_status'])
                            audio_count = int(rr['audio_count'] or 0)
                            audio_count_map[scope_key] = audio_count

                    k2.execute(f'''
                        SELECT
                            upc,
                            COALESCE(row_status, '') as row_status,
                            COUNT(*) as video_count
                        FROM items_prep_media
                        WHERE upc IN ({placeholders})
                          AND COALESCE(media_type, '') = 'video'
                        GROUP BY upc, COALESCE(row_status, '')
                    ''', tuple(page_upcs))
                    for rr in k2.fetchall():
                        raw_upc = rr['upc']
                        if raw_upc:
                            scope_key = ss_normalization._items_to_list_asset_scope(raw_upc, rr['row_status'])
                            video_count_map[scope_key] = int(rr['video_count'] or 0)

                    # Active (not deleted) photo counts for each UPC on the current page.
                    k2.execute(f'''
                        SELECT upc, COALESCE(row_status, '') as row_status, COUNT(*) as photo_count
                        FROM items_prep_images
                        WHERE upc IN ({placeholders})
                          AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at, '')) = '')
                        GROUP BY upc, COALESCE(row_status, '')
                    ''', tuple(page_upcs))
                    for rr in k2.fetchall():
                        raw_upc = rr['upc']
                        if raw_upc:
                            scope_key = ss_normalization._items_to_list_asset_scope(raw_upc, rr['row_status'])
                            photo_count_map[scope_key] = int(rr['photo_count'] or 0)
        except Exception:
            status_map = {}
            note_map = {}
            set_note_map = {}
            photo_count_map = {}
            audio_count_map = {}
            video_count_map = {}
            upc_lot_count_map = {}
            bol_lot_rows_by_upc = {}

        def _safe_items_to_list_int(value, fallback=0):
            try:
                return int(value)
            except Exception:
                return fallback

        def _items_to_list_resolve_prep_state(upc_value, lot_value='', *, bol_row=None, joined_row=None):
            row_upc = ss_normalization._normalize_upc(upc_value)
            row_lot = ss_normalization._normalize_lot_number(lot_value)
            ref = bol_row or joined_row or {}
            good_bucket = _safe_items_to_list_int(ref.get('good_qty'), 0)
            bad_bucket = _safe_items_to_list_int(ref.get('bad_qty'), 0)
            quantity_fallback = _safe_items_to_list_int(
                ref.get('quantity') if ref.get('quantity') is not None else ref.get('bol_quantity'),
                1
            )
            multi_lot = upc_lot_count_map.get(row_upc, 0) > 1
            exact_state = status_map.get((row_upc, row_lot))
            shared_state = status_map.get((row_upc, '')) if not multi_lot else None
            chosen_state = exact_state or shared_state

            explicit_status = ''
            explicit_reason = ''
            explicit_note = ''
            explicit_updated = ''
            explicit_qty = None

            if chosen_state:
                explicit_status = chosen_state.get('prep_status')
                explicit_reason = chosen_state.get('prep_reason')
                explicit_note = chosen_state.get('prep_note')
                explicit_updated = chosen_state.get('prep_updated_at')
                explicit_qty = chosen_state.get('prep_quantity')
            else:
                joined_prep_lot = ss_normalization._normalize_lot_number((joined_row or {}).get('prep_lot_number'))
                joined_status = (joined_row or {}).get('prep_status')
                ambiguous_shared = multi_lot and row_lot and not joined_prep_lot and bool(ss_normalization._normalize_prep_row_status(joined_status))
                if joined_row and not ambiguous_shared:
                    explicit_status = joined_row.get('prep_status')
                    explicit_reason = joined_row.get('prep_reason')
                    explicit_note = joined_row.get('prep_note')
                    explicit_updated = joined_row.get('prep_updated_at')
                    explicit_qty = joined_row.get('prep_quantity')

            raw_status = ss_normalization._normalize_prep_row_status(explicit_status)
            invalid_base_special = raw_status in ('bad', 'return') and not ss_normalization._is_items_to_list_suffixed_upc(row_upc)
            status_value = ss_normalization._items_to_list_effective_status(
                row_upc,
                explicit_status,
                good_qty=good_bucket,
                bad_qty=bad_bucket
            )

            if status_value in ('good', 'bad', 'return') and status_value != raw_status:
                if status_value == 'good':
                    explicit_qty = good_bucket
                elif status_value == 'bad':
                    explicit_qty = bad_bucket
                if invalid_base_special:
                    explicit_reason = ''
                    explicit_note = ''

            if not status_value:
                unchecked_raw = ref.get('unchecked_qty')
                if unchecked_raw is None:
                    original_bucket = _safe_items_to_list_int(ref.get('original_qty'), quantity_fallback)
                    unchecked_bucket = max(0, original_bucket - good_bucket - bad_bucket)
                else:
                    unchecked_bucket = max(0, _safe_items_to_list_int(unchecked_raw, 0))

                if unchecked_bucket > 0:
                    status_value = 'unchecked'
                    explicit_status = 'unchecked'
                    explicit_qty = unchecked_bucket
                else:
                    status_value = ss_normalization._items_to_list_bucket_status(row_upc, good_qty=good_bucket, bad_qty=bad_bucket)
                    if status_value == 'good':
                        explicit_status = 'good'
                        explicit_qty = good_bucket
                    elif status_value == 'bad':
                        explicit_status = 'bad'
                        explicit_qty = bad_bucket

            if explicit_qty is not None:
                display_qty = max(0, _safe_items_to_list_int(explicit_qty, quantity_fallback))
            elif status_value == 'good':
                display_qty = max(0, _safe_items_to_list_int(good_bucket, quantity_fallback))
            elif status_value in ('bad', 'return'):
                fallback_bad_qty = bad_bucket if bad_bucket > 0 else quantity_fallback
                display_qty = max(0, _safe_items_to_list_int(fallback_bad_qty, quantity_fallback))
            else:
                display_qty = max(0, quantity_fallback)

            return {
                'status': status_value,
                'prep_status': explicit_status,
                'prep_reason': explicit_reason,
                'prep_note': explicit_note,
                'prep_updated_at': explicit_updated,
                'prep_quantity': display_qty,
                'lotless_present': (row_upc, '') in status_map,
                'lot_count': upc_lot_count_map.get(row_upc, 0)
            }

        # Enrich rows with status map for any missing joins
        def status_of(row):
            row_upc = ss_normalization._normalize_upc(row.get('upc'))
            lot_state = _items_to_list_resolve_prep_state(
                row.get('upc'),
                row.get('lot_number'),
                bol_row=row,
                joined_row=row
            )
            st = lot_state.get('status') or ''
            row['prep_status'] = lot_state.get('prep_status') or ''
            row['prep_reason'] = lot_state.get('prep_reason') or ''
            row['prep_note'] = lot_state.get('prep_note') or ''
            row['prep_updated_at'] = lot_state.get('prep_updated_at') or ''
            row['prep_quantity'] = lot_state.get('prep_quantity')
            row['prep_lotless_present'] = bool(lot_state.get('lotless_present'))
            row['prep_lot_count'] = int(lot_state.get('lot_count') or 0)

            scope_key = ss_normalization._items_to_list_asset_scope(row_upc, st)
            if 'prep_note_count' not in row:
                row['prep_note_count'] = note_map.get(scope_key, 0)
            if 'prep_set_note' not in row:
                row['prep_set_note'] = set_note_map.get(scope_key, 0)
            if 'prep_photo_count' not in row:
                row['prep_photo_count'] = photo_count_map.get(scope_key, 0)
            if 'prep_audio_count' not in row:
                row['prep_audio_count'] = audio_count_map.get(scope_key, 0)
            if 'prep_video_count' not in row:
                row['prep_video_count'] = video_count_map.get(scope_key, 0)

            return st if st in ('good', 'bad', 'return') else ''
        
        # Apply status enrichment and drop any remaining unchecked/legacy rows.
        visible_rows = []
        for r in rows:
            if status_of(r) in ('good', 'bad', 'return'):
                visible_rows.append(r)
        rows = visible_rows

        prep_lot_breakdown_by_upc = {}
        for upc_key, lot_rows in bol_lot_rows_by_upc.items():
            breakdown_rows = []
            seen_lots = set()
            for lot_row in lot_rows:
                lot_n = ss_normalization._normalize_lot_number(lot_row.get('lot_number'))
                if lot_n in seen_lots:
                    continue
                seen_lots.add(lot_n)
                lot_state = _items_to_list_resolve_prep_state(
                    lot_row.get('upc'),
                    lot_row.get('lot_number'),
                    bol_row=lot_row
                )
                if lot_state.get('status') not in ('good', 'bad', 'return'):
                    continue
                breakdown_rows.append({
                    'lot_number': lot_n,
                    'lot_label': lot_n or 'LOTLESS',
                    'lot_quantity': max(0, _safe_items_to_list_int(lot_row.get('bol_quantity'), 0)),
                    'import_date': lot_row.get('import_date') or '',
                    'prep_quantity': max(0, _safe_items_to_list_int(lot_state.get('prep_quantity'), 0)),
                    'prep_status': lot_state.get('status') or '',
                    'prep_updated_at': lot_state.get('prep_updated_at') or '',
                    'is_lotless': not bool(lot_n)
                })

            if (upc_key, '') in status_map and '' not in seen_lots:
                lotless_state = status_map.get((upc_key, '')) or {}
                lotless_status = ss_normalization._items_to_list_effective_status(
                    upc_key,
                    lotless_state.get('prep_status'),
                    good_qty=0,
                    bad_qty=0
                )
                if lotless_status in ('good', 'bad', 'return'):
                    breakdown_rows.append({
                        'lot_number': '',
                        'lot_label': 'LOTLESS',
                        'lot_quantity': 0,
                        'import_date': '',
                        'prep_quantity': max(0, _safe_items_to_list_int(lotless_state.get('prep_quantity'), 0)),
                        'prep_status': lotless_status,
                        'prep_updated_at': lotless_state.get('prep_updated_at') or '',
                        'is_lotless': True
                    })

            breakdown_rows.sort(key=lambda entry: (
                0 if entry.get('is_lotless') else 1,
                str(entry.get('import_date') or ''),
                str(entry.get('prep_updated_at') or '')
            ), reverse=True)
            prep_lot_breakdown_by_upc[upc_key] = breakdown_rows
        
        # Normalize for UI
        results = []
        for r in rows:
            # Determine last_edited: prefer items_prep_status.updated_at, fall back to import_date
            last_edited = r.get('prep_updated_at') or r.get('import_date') or ''
            status = (r.get('prep_status') or '').strip().lower()
            if status not in ('good', 'bad', 'return'):
                continue
            upc_raw = r.get('upc') or ''
            warehouse_base_upc = _warehouse_base_upc(r.get('upc'))
            warehouse_available = max(0, int(warehouse_available_by_base.get(warehouse_base_upc, 0) or 0))
            upc_exact_key = ss_normalization._normalize_upc_preserve_suffix_for_match(upc_raw)
            warehouse_exact = max(0, int(warehouse_exact_by_upc.get(upc_exact_key, 0) or 0))
            warehouse_locations = warehouse_locations_by_base.get(warehouse_base_upc, []) or []
            warehouse_loc_qty_map = warehouse_location_qty_by_base.get(warehouse_base_upc, {}) or {}
            warehouse_location_details = []
            seen_warehouse_locations = set()
            for raw_loc in warehouse_locations:
                loc = str(raw_loc or '').strip()
                if not loc or loc in seen_warehouse_locations:
                    continue
                seen_warehouse_locations.add(loc)
                try:
                    loc_qty = int(warehouse_loc_qty_map.get(loc, 0) or 0)
                except Exception:
                    loc_qty = 0
                warehouse_location_details.append({
                    'location': loc,
                    'quantity': max(0, loc_qty)
                })
            extra_location_rows = []
            for raw_loc, raw_qty in warehouse_loc_qty_map.items():
                loc = str(raw_loc or '').strip()
                if not loc:
                    continue
                try:
                    loc_qty = int(raw_qty or 0)
                except Exception:
                    loc_qty = 0
                extra_location_rows.append((loc, max(0, loc_qty)))
            extra_location_rows.sort(key=lambda item: (-item[1], item[0].lower()))
            for loc, loc_qty in extra_location_rows:
                if loc in seen_warehouse_locations:
                    continue
                seen_warehouse_locations.add(loc)
                warehouse_location_details.append({
                    'location': loc,
                    'quantity': loc_qty
                })

            prep_qty = r.get('prep_quantity')
            if prep_qty is not None:
                display_qty = max(0, _safe_items_to_list_int(prep_qty, _safe_items_to_list_int(r.get('quantity') or 1, 1)))
            elif status == 'good':
                display_qty = max(0, _safe_items_to_list_int(r.get('good_qty'), _safe_items_to_list_int(r.get('quantity') or 1, 1)))
            elif status in ('bad', 'return'):
                display_qty = max(0, _safe_items_to_list_int(r.get('bad_qty'), _safe_items_to_list_int(r.get('quantity') or 1, 1)))
            elif status == 'unchecked':
                unchecked_qty = r.get('unchecked_qty')
                if unchecked_qty is None:
                    original_qty = _safe_items_to_list_int(r.get('original_qty'), _safe_items_to_list_int(r.get('quantity') or 1, 1))
                    good_qty = _safe_items_to_list_int(r.get('good_qty'), 0)
                    bad_qty = _safe_items_to_list_int(r.get('bad_qty'), 0)
                    display_qty = max(0, original_qty - good_qty - bad_qty)
                else:
                    display_qty = max(0, _safe_items_to_list_int(unchecked_qty, _safe_items_to_list_int(r.get('quantity') or 1, 1)))
            else:
                display_qty = max(0, _safe_items_to_list_int(r.get('quantity') or 1, 1))

            upc_norm = ss_normalization._normalize_upc(upc_raw)
            row_lot_n = ss_normalization._normalize_lot_number(r.get('lot_number'))
            prep_lot_breakdown = list(prep_lot_breakdown_by_upc.get(upc_norm, []) or [])
            if status == 'return' and ss_normalization._is_items_to_list_suffixed_upc(upc_norm):
                return_base_upc = upc_norm.rsplit('-', 1)[0].strip()
                return_base_candidates = (
                    return_base_upc,
                    ss_normalization._strip_leading_zeros_numeric(return_base_upc)
                )
                for return_base_key in return_base_candidates:
                    base_breakdown = prep_lot_breakdown_by_upc.get(return_base_key, []) or []
                    real_lot_breakdown = [
                        dict(entry) for entry in base_breakdown
                        if ss_normalization._normalize_lot_number(entry.get('lot_number'))
                    ]
                    if real_lot_breakdown:
                        prep_lot_breakdown = real_lot_breakdown
                        break
                else:
                    # Never present the return row's synthetic lotless prep
                    # record as LOT history.
                    prep_lot_breakdown = [
                        dict(entry) for entry in prep_lot_breakdown
                        if ss_normalization._normalize_lot_number(entry.get('lot_number'))
                    ]
            prep_total_quantity = 0
            prep_current_lot_quantity = display_qty
            prep_breakdown_keys = set()
            matched_current_lot = False
            for prep_entry in prep_lot_breakdown:
                prep_entry_lot = ss_normalization._normalize_lot_number(prep_entry.get('lot_number'))
                prep_entry_key = prep_entry_lot or '__lotless__'
                if prep_entry_key in prep_breakdown_keys:
                    continue
                prep_breakdown_keys.add(prep_entry_key)
                entry_qty = max(0, _safe_items_to_list_int(prep_entry.get('prep_quantity'), 0))
                prep_total_quantity += entry_qty
                if prep_entry_lot == row_lot_n:
                    prep_current_lot_quantity = entry_qty
                    matched_current_lot = True
            if prep_lot_breakdown and not matched_current_lot and not row_lot_n:
                lotless_entry = next(
                    (entry for entry in prep_lot_breakdown if not ss_normalization._normalize_lot_number(entry.get('lot_number'))),
                    None
                )
                if lotless_entry is not None:
                    prep_current_lot_quantity = max(
                        0,
                        _safe_items_to_list_int(lotless_entry.get('prep_quantity'), display_qty)
                    )
                    matched_current_lot = True
            prep_total_quantity = max(prep_total_quantity, display_qty)
            prep_multi_lot = len(prep_breakdown_keys) > 1
            if status == 'return':
                # LOT rows describe where the base barcode appeared; the return
                # quantity itself is not allocated back across those LOTS.
                prep_total_quantity = display_qty
                prep_current_lot_quantity = display_qty
                prep_multi_lot = len(prep_breakdown_keys) > 1

            # Check if item has notes in items_prep_notes table
            has_notes = r.get('prep_note_count', 0) > 0
            audio_count = int(r.get('prep_audio_count') or 0)
            photo_count = int(r.get('prep_photo_count') or 0)
            video_count = int(r.get('prep_video_count') or 0)
            
            results.append({
                'id': r.get('id'),
                'title': r.get('item_description') or '',
                'image': r.get('image_url') or '',
                'upc': upc_raw,
                'lot_number': r.get('lot_number') or '',
                'bol_number': r.get('bol_number') or '',
                'import_date': r.get('import_date') or '',
                'last_edited': last_edited,
                'defect': (r.get('prep_reason') or ''),
                'status': status,
                'list_status': (r.get('list_status') or ''),
                'temporary': r.get('temporary'),
                'quantity': display_qty,
                'prep_current_lot': row_lot_n,
                'prep_current_label': row_lot_n or ('ALL LOTS' if status == 'return' else 'LOTLESS'),
                'prep_lot_breakdown': prep_lot_breakdown,
                'prep_lotless_present': bool(r.get('prep_lotless_present')) if status != 'return' else False,
                'prep_total_quantity': prep_total_quantity,
                'prep_current_lot_quantity': prep_current_lot_quantity,
                'prep_multi_lot': prep_multi_lot,
                'prep_grouped_all_lots': False,
                'selection_locked': False,
                'diag_locked': False,
                'marketplace_edit_locked': False,
                'warehouse_base_upc': warehouse_base_upc,
                'warehouse_available': warehouse_available,
                'warehouse_exact': warehouse_exact,
                'warehouse_locations': warehouse_locations,
                'warehouse_location_details': warehouse_location_details,
                'note': 'yes' if has_notes else '',
                'set_note_flag': bool(r.get('prep_set_note') or 0),
                'audio_count': audio_count,
                'photo_count': photo_count,
                'video_count': video_count,
                # Marketplace listing columns
                'listed_amazon': r.get('listed_amazon'),
                'listed_amazon_date': r.get('listed_amazon_date'),
                'listed_amazon_source': r.get('listed_amazon_source'),
                'listed_ebay': r.get('listed_ebay'),
                'listed_ebay_date': r.get('listed_ebay_date'),
                'listed_ebay_source': r.get('listed_ebay_source'),
                'listed_facebook': r.get('listed_facebook'),
                'listed_facebook_date': r.get('listed_facebook_date'),
                'listed_facebook_source': r.get('listed_facebook_source')
            })
        ss_listing_lifecycle._apply_marketplace_status_overlay(results)
        if group_multi_lot_rows:
            def _group_items_to_list_all_lots(rows_local):
                grouped = {}
                order = []

                def _group_key(item):
                    return (
                        ss_normalization._normalize_upc_preserve_suffix_for_match(item.get('upc')),
                        str(item.get('status') or '').strip().lower()
                    )

                for item in rows_local:
                    key = _group_key(item)
                    if key not in grouped:
                        grouped[key] = []
                        order.append(key)
                    grouped[key].append(item)

                merged_rows = []
                for key in order:
                    bucket = grouped.get(key) or []
                    if len(bucket) <= 1:
                        merged_rows.extend(bucket)
                        continue

                    base_item = dict(bucket[0])
                    breakdown = list(base_item.get('prep_lot_breakdown') or [])
                    breakdown_total = 0
                    breakdown_keys = set()
                    lotless_present = False
                    for entry in breakdown:
                        lot_key = ss_normalization._normalize_lot_number(entry.get('lot_number'))
                        dedupe_key = lot_key or '__lotless__'
                        if dedupe_key in breakdown_keys:
                            continue
                        breakdown_keys.add(dedupe_key)
                        breakdown_total += max(0, _safe_items_to_list_int(entry.get('prep_quantity'), 0))
                        if not lot_key:
                            lotless_present = True

                    if breakdown_total <= 0:
                        breakdown_total = sum(max(0, _safe_items_to_list_int(item.get('quantity'), 0)) for item in bucket)

                    unique_defects = []
                    seen_defects = set()
                    for item in bucket:
                        defect_text = str(item.get('defect') or '').strip()
                        if defect_text and defect_text not in seen_defects:
                            seen_defects.add(defect_text)
                            unique_defects.append(defect_text)

                    def _merge_listed_state(marketplace):
                        field = f'listed_{marketplace}'
                        values = [1 if int(item.get(field) or 0) == 1 else 0 for item in bucket]
                        all_listed = all(values)
                        any_listed = any(values)
                        base_item[field] = 1 if all_listed else 0
                        base_item[f'{field}_mixed'] = bool(any_listed and not all_listed)
                        if base_item[f'{field}_mixed']:
                            base_item[f'{field}_date'] = ''
                            base_item[f'{field}_source_label'] = 'Mixed LOTS'

                    for marketplace_name in ('amazon', 'ebay', 'facebook'):
                        _merge_listed_state(marketplace_name)

                    latest_last_edited = max(str(item.get('last_edited') or '') for item in bucket)
                    latest_import_date = max(str(item.get('import_date') or '') for item in bucket)

                    base_item['lot_number'] = ''
                    base_item['bol_number'] = ''
                    base_item['quantity'] = max(0, breakdown_total)
                    base_item['last_edited'] = latest_last_edited or base_item.get('last_edited') or ''
                    base_item['import_date'] = latest_import_date or base_item.get('import_date') or ''
                    base_item['defect'] = ' | '.join(unique_defects)
                    base_item['note'] = 'yes' if any(str(item.get('note') or '').strip().lower() == 'yes' for item in bucket) else ''
                    base_item['set_note_flag'] = any(bool(item.get('set_note_flag')) for item in bucket)
                    base_item['audio_count'] = max(max(0, _safe_items_to_list_int(item.get('audio_count'), 0)) for item in bucket)
                    base_item['photo_count'] = max(max(0, _safe_items_to_list_int(item.get('photo_count'), 0)) for item in bucket)
                    base_item['video_count'] = max(max(0, _safe_items_to_list_int(item.get('video_count'), 0)) for item in bucket)
                    base_item['prep_current_lot'] = ''
                    base_item['prep_current_label'] = 'ALL LOTS'
                    base_item['prep_total_quantity'] = max(0, breakdown_total)
                    base_item['prep_current_lot_quantity'] = max(0, breakdown_total)
                    base_item['prep_multi_lot'] = len(breakdown_keys) > 1
                    base_item['prep_grouped_all_lots'] = True
                    base_item['prep_lotless_present'] = bool(base_item.get('prep_lotless_present')) or lotless_present
                    base_item['selection_locked'] = True
                    base_item['diag_locked'] = True
                    base_item['marketplace_edit_locked'] = True
                    merged_rows.append(base_item)

                return merged_rows

            results = _group_items_to_list_all_lots(results)
        if needs_enriched_post_filter:
            def _safe_nonnegative_int(value):
                try:
                    return max(0, int(value))
                except Exception:
                    return 0

            def _warehouse_location_count(item):
                locations = set()
                raw_details = item.get('warehouse_location_details')
                if isinstance(raw_details, list):
                    for entry in raw_details:
                        if isinstance(entry, dict):
                            loc_val = entry.get('location') or entry.get('code')
                        else:
                            loc_val = entry
                        loc = str(loc_val or '').strip()
                        if loc:
                            locations.add(loc)
                if not locations:
                    raw_locations = item.get('warehouse_locations')
                    if isinstance(raw_locations, list):
                        for loc_val in raw_locations:
                            loc = str(loc_val or '').strip()
                            if loc:
                                locations.add(loc)
                return len(locations)

            filtered_results = []
            for item in results:
                warehouse_available = _safe_nonnegative_int(item.get('warehouse_available'))
                location_count = _warehouse_location_count(item)

                warehouse_match = True
                if warehouse_filter == 'with_quantity':
                    warehouse_match = warehouse_available > 0
                elif warehouse_filter == 'without_quantity':
                    warehouse_match = warehouse_available <= 0
                elif warehouse_filter == 'multiple_locations':
                    warehouse_match = location_count > 1

                if warehouse_match:
                    filtered_results.append(item)

            total = len(filtered_results)
            unique_key_pairs = set()
            total_quantity = 0
            for item in filtered_results:
                unique_key_pairs.add((ss_normalization._normalize_upc(item.get('upc')), ss_normalization._normalize_lot_number(item.get('lot_number'))))
                total_quantity += _safe_nonnegative_int(item.get('quantity'))
            unique_items = len(unique_key_pairs)

            paged_results = filtered_results[offset: offset + limit]
            return jsonify({
                'results': paged_results,
                'total': total,
                'unique_items': unique_items,
                'total_quantity': total_quantity,
                'page': page,
                'limit': limit
            })

        if group_multi_lot_rows:
            total = len(results)
            unique_items = len(results)
            total_quantity = sum(max(0, _safe_items_to_list_int(item.get('quantity'), 0)) for item in results)
            paged_results = results[offset: offset + limit]
            return jsonify({
                'results': paged_results,
                'total': total,
                'unique_items': unique_items,
                'total_quantity': total_quantity,
                'page': page,
                'limit': limit
            })

        return jsonify({'results': results, 'total': total, 'unique_items': unique_items, 'total_quantity': total_quantity, 'page': page, 'limit': limit})
    except Exception as e:
        return jsonify({'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_bulk_delete_bol_items():
    """Delete or reset selected BOL items.
    - Duplicates (UPC with -N suffix) or custom 777 barcodes: DELETE
    - Base barcodes: RESET to rawbol.db default state
    """
    try:
        data = request.get_json() or {}
        items = data.get('items', [])
        ids = data.get('ids', [])
        if not items and ids:
            items = [{'id': x} for x in ids]
        if not items:
            return jsonify({'success': False, 'error': 'No items provided'}), 400

        import re
        ss_prep_schema._ensure_items_prep_tables()
        ss_listing_lifecycle._ensure_bol_list_status_column()

        # Normalize IDs and load canonical rows from bol_items.
        item_ids = []
        for item in items:
            try:
                if item.get('id') is not None:
                    item_ids.append(int(item.get('id')))
            except Exception:
                continue

        if not item_ids:
            return jsonify({'success': False, 'error': 'No valid item ids provided'}), 400

        conn = sqlite3.connect('bol.db', isolation_level='IMMEDIATE')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ph = ','.join('?' for _ in item_ids)
        cur.execute(f'''
            SELECT id, upc, COALESCE(lot_number, '') AS lot_number
            FROM bol_items
            WHERE id IN ({ph})
        ''', tuple(item_ids))
        target_rows = [dict(r) for r in cur.fetchall()]
        if not target_rows:
            conn.close()
            return jsonify({'success': False, 'error': 'No matching items found'}), 404

        deletable_rows = []
        reset_rows = []
        for row in target_rows:
            upc = str(row.get('upc') or '').strip()
            if re.search(r'-\d+$', upc) or upc.startswith('777'):
                deletable_rows.append(row)
            else:
                reset_rows.append(row)

        deleted = 0
        reset_count = 0

        # Delete duplicates/custom entries.
        if deletable_rows:
            delete_ids = [int(r['id']) for r in deletable_rows]
            del_ph = ','.join('?' for _ in delete_ids)
            cur.execute(f'DELETE FROM bol_items WHERE id IN ({del_ph})', tuple(delete_ids))
            deleted = cur.rowcount

            for row in deletable_rows:
                upc = row['upc']
                lot = ss_normalization._normalize_lot_number(row.get('lot_number'))
                cur.execute('''
                    DELETE FROM items_prep_status
                    WHERE upc = ? COLLATE NOCASE
                      AND (
                        COALESCE(lot_number, '') = ? COLLATE NOCASE
                        OR (? <> '' AND COALESCE(lot_number, '') = '')
                      )
                ''', (upc, lot, lot))
                ss_prep_media._items_prep_delete_diagnostic_assets(cur, upc)

        # Reset base entries by row id and lot-specific raw quantity.
        if reset_rows:
            raw_conn = sqlite3.connect('rawbol.db')
            raw_cur = raw_conn.cursor()
            for row in reset_rows:
                row_id = int(row['id'])
                upc = str(row.get('upc') or '').strip()
                lot = ss_normalization._normalize_lot_number(row.get('lot_number'))
                try:
                    rawbol_qty = 1
                    if lot:
                        raw_cur.execute('''
                            SELECT SUM(COALESCE(quantity, 0))
                            FROM raw_bol_items
                            WHERE upc = ? COLLATE NOCASE
                              AND lot_number = ? COLLATE NOCASE
                        ''', (upc, lot))
                    else:
                        raw_cur.execute('''
                            SELECT SUM(COALESCE(quantity, 0))
                            FROM raw_bol_items
                            WHERE upc = ? COLLATE NOCASE
                        ''', (upc,))
                    raw_row = raw_cur.fetchone()
                    if raw_row and raw_row[0]:
                        rawbol_qty = int(raw_row[0])

                    status_row = ss_warehouse_allocations._select_prep_status_row(cur, upc, lot, columns='status')
                    is_good = bool(status_row and str(status_row[0] or '').strip().lower() == 'good')
                    if is_good:
                        cur.execute('''
                            UPDATE items_prep_status
                            SET status = 'unchecked', reason = '', note = '', quantity = ?, updated_at = datetime('now')
                            WHERE upc = ? COLLATE NOCASE
                              AND COALESCE(lot_number, '') = ? COLLATE NOCASE
                        ''', (rawbol_qty, upc, lot))
                        if cur.rowcount == 0 and lot:
                            cur.execute('''
                                UPDATE items_prep_status
                                SET status = 'unchecked', reason = '', note = '', quantity = ?, updated_at = datetime('now')
                                WHERE upc = ? COLLATE NOCASE
                                  AND COALESCE(lot_number, '') = ''
                            ''', (rawbol_qty, upc))
                    else:
                        cur.execute('''
                            DELETE FROM items_prep_status
                            WHERE upc = ? COLLATE NOCASE
                              AND (
                                COALESCE(lot_number, '') = ? COLLATE NOCASE
                                OR (? <> '' AND COALESCE(lot_number, '') = '')
                              )
                        ''', (upc, lot, lot))

                    ss_prep_media._items_prep_delete_diagnostic_assets(cur, upc)

                    cur.execute('PRAGMA table_info(bol_items)')
                    cols = [col[1].lower() for col in cur.fetchall()]
                    has_new_cols = 'original_qty' in cols and 'unchecked_qty' in cols

                    if has_new_cols:
                        cur.execute('''
                            UPDATE bol_items
                            SET quantity = ?,
                                original_qty = ?,
                                good_qty = 0,
                                bad_qty = 0,
                                unchecked_qty = ?,
                                list_status = NULL,
                                listed_amazon = 0,
                                listed_amazon_date = NULL,
                                listed_amazon_source = NULL,
                                listed_ebay = 0,
                                listed_ebay_date = NULL,
                                listed_ebay_source = NULL,
                                listed_facebook = 0,
                                listed_facebook_date = NULL,
                                listed_facebook_qty = NULL,
                                listed_facebook_source = NULL
                            WHERE id = ?
                        ''', (rawbol_qty, rawbol_qty, rawbol_qty, row_id))
                    else:
                        cur.execute('UPDATE bol_items SET quantity = ?, list_status = NULL WHERE id = ?', (rawbol_qty, row_id))

                    reset_count += 1
                except Exception as e:
                    print(f'Warning: Failed to reset row {row_id} ({upc}, lot={lot}): {e}')
            raw_conn.close()

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'deleted': deleted, 'reset': reset_count})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_set_selected_lot():
    """Set the selected LOT in session storage."""
    try:
        data = request.get_json() or {}
        lot_number = data.get('lot_number', '').strip()
        
        if not lot_number:
            return jsonify({'success': False, 'error': 'Missing lot_number'}), 400
        
        session['selected_lot'] = lot_number
        return jsonify({'success': True, 'selected_lot': lot_number})
    
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_bol_lots():
    """Return list of available lots plus LOTLESS when blank-lot rows exist."""
    conn = None
    bol_conn = None
    try:
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        lots = []
        # Check if raw_bol_items table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='raw_bol_items'")
        if cur.fetchone():
            # Get distinct LOT numbers with their import dates, filtered and sorted
            cur.execute('''
                SELECT 
                    lot_number,
                    MAX(import_date) as import_date
                FROM raw_bol_items
                WHERE lot_number IS NOT NULL 
                    AND TRIM(COALESCE(lot_number, '')) != ''
                    AND LOWER(lot_number) NOT IN ('nan', 'none', 'null')
                GROUP BY lot_number
                ORDER BY MAX(import_date) DESC
            ''')
            
            for r in cur.fetchall():
                # Format date as YYYY-MM-DD
                date_str = r['import_date'] or ''
                if date_str:
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                        date_str = dt.strftime('%Y-%m-%d')
                    except Exception:
                        pass
                
                lots.append({
                    'lot_number': r['lot_number'],
                    'import_date': date_str
                })

        lotless_exists = False
        lotless_import_date = ''
        returns_exists = False
        try:
            bol_conn = sqlite3.connect('bol.db')
            bol_conn.row_factory = sqlite3.Row
            bol_cur = bol_conn.cursor()
            bol_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bol_items'")
            if bol_cur.fetchone():
                bol_cur.execute('''
                    SELECT MAX(import_date) AS import_date
                    FROM bol_items
                    WHERE TRIM(COALESCE(lot_number, '')) = ''
                ''')
                lotless_row = bol_cur.fetchone()
                lotless_import_date = str((lotless_row['import_date'] if lotless_row else '') or '').strip()
                lotless_exists = bool(lotless_import_date)
                if not lotless_exists:
                    bol_cur.execute('''
                        SELECT 1
                        FROM bol_items
                        WHERE TRIM(COALESCE(lot_number, '')) = ''
                        LIMIT 1
                    ''')
                    lotless_exists = bol_cur.fetchone() is not None
                if lotless_import_date:
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(lotless_import_date.replace('Z', '+00:00'))
                        lotless_import_date = dt.strftime('%Y-%m-%d')
                    except Exception:
                        pass
            bol_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='items_prep_status'")
            if bol_cur.fetchone():
                bol_cur.execute('''
                    SELECT 1
                    FROM items_prep_status
                    WHERE LOWER(TRIM(COALESCE(status, ''))) = 'return'
                      AND INSTR(COALESCE(upc, ''), '-') > 0
                    LIMIT 1
                ''')
                returns_exists = bol_cur.fetchone() is not None
        except Exception:
            lotless_exists = False
            lotless_import_date = ''
            returns_exists = False

        if lotless_exists:
            lots.append({
                'lot_number': 'LOTLESS',
                'import_date': lotless_import_date
            })
        if returns_exists:
            lots.append({
                'lot_number': 'RETURNS',
                'import_date': ''
            })

        # Return lots with current selected lot from session
        selected_lot = session.get('selected_lot')
        # If no lot selected, default to the newest (first in list)
        if not selected_lot and lots:
            selected_lot = lots[0]['lot_number']
            session['selected_lot'] = selected_lot
        
        return jsonify({'lots': lots, 'selected_lot': selected_lot})
    except Exception as e:
        return jsonify({'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()
        if bol_conn is not None:
            bol_conn.close()


def api_bol_items_set_list_status():
    """Set marketplace listing status for a BOL item.
    JSON: { id?, upc?, lot_number?, marketplace, listed, quantity (for facebook) }
    marketplace in ['amazon', 'ebay', 'facebook'] and listed is boolean.
    """
    conn = None
    try:
        data = request.get_json() or {}
        upc = ss_normalization._strip_leading_zeros_numeric(ss_normalization._normalize_upc(data.get('upc')))
        item_id = data.get('id')
        if item_id is not None and str(item_id).strip() != '':
            try:
                item_id = int(item_id)
            except Exception:
                return jsonify({'success': False, 'error': 'Invalid id'}), 400
            if item_id <= 0:
                return jsonify({'success': False, 'error': 'Invalid id'}), 400
        else:
            item_id = None

        lot_explicit = False
        lot_raw = ''
        if isinstance(data, dict):
            if 'lot_number' in data:
                lot_explicit = True
                lot_raw = data.get('lot_number')
            elif 'lot' in data:
                lot_explicit = True
                lot_raw = data.get('lot')
        lot_number = ss_normalization._normalize_lot_number(lot_raw)
        if not lot_number and not lot_explicit:
            lot_number = ss_warehouse_allocations._preferred_lot_from_request(data)
        marketplace = (data.get('marketplace') or '').strip().lower()
        listed = ss_normalization._coerce_bool(data.get('listed', True))
        try:
            quantity = int(data.get('quantity', 1)) if data.get('quantity') is not None else 1
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid quantity'}), 400
        if quantity < 1:
            quantity = 1
        source = ss_normalization._normalize_listing_source(data.get('source'), default='user')

        print(f"[list_status] id={item_id} UPC: {upc}, LOT: {lot_number}, Marketplace: {marketplace}, Listed: {listed}, Qty: {quantity}, Source: {source}")

        if not upc and item_id is None:
            return jsonify({'success': False, 'error': 'Missing upc or id'}), 400
        if marketplace not in ('amazon', 'ebay', 'facebook'):
            return jsonify({'success': False, 'error': 'Invalid marketplace. Must be amazon, ebay, or facebook'}), 400

        # Ensure columns exist before trying to update
        ss_listing_lifecycle._ensure_bol_list_status_column()

        conn = sqlite3.connect('bol.db', isolation_level='IMMEDIATE')
        cur = conn.cursor()

        if item_id is None:
            if not upc:
                return jsonify({'success': False, 'error': 'Missing upc or id'}), 400
            if lot_number:
                cur.execute('''
                    SELECT COUNT(*)
                    FROM bol_items
                    WHERE upc = ? COLLATE NOCASE
                      AND lot_number = ? COLLATE NOCASE
                ''', (upc, lot_number))
                lot_row_count = int((cur.fetchone() or [0])[0] or 0)
                if lot_row_count > 1:
                    return jsonify({
                        'success': False,
                        'error': 'Multiple rows found for this UPC+LOT. Please retry from the latest Items-to-List page so row id is included.'
                    }), 400
            else:
                cur.execute('SELECT COUNT(*) FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
                row_count = int((cur.fetchone() or [0])[0] or 0)
                if row_count > 1:
                    return jsonify({
                        'success': False,
                        'error': 'Multiple rows found for this UPC. Please provide lot_number or row id.'
                    }), 400

        if item_id is not None:
            cur.execute('''
                SELECT id, upc, lot_number, COALESCE(listed_facebook,0), COALESCE(listed_facebook_qty,1), listed_facebook_date
                FROM bol_items
                WHERE id = ?
                LIMIT 1
            ''', (item_id,))
        else:
            if lot_number:
                cur.execute('''
                    SELECT id, upc, lot_number, COALESCE(listed_facebook,0), COALESCE(listed_facebook_qty,1), listed_facebook_date
                    FROM bol_items
                    WHERE upc = ? COLLATE NOCASE
                      AND lot_number = ? COLLATE NOCASE
                    ORDER BY import_date DESC, id DESC
                    LIMIT 1
                ''', (upc, lot_number))
            else:
                cur.execute('''
                    SELECT id, upc, lot_number, COALESCE(listed_facebook,0), COALESCE(listed_facebook_qty,1), listed_facebook_date
                    FROM bol_items
                    WHERE upc = ? COLLATE NOCASE
                    ORDER BY import_date DESC, id DESC
                    LIMIT 1
                ''', (upc,))
        target_row = cur.fetchone()
        if not target_row:
            if item_id is not None:
                return jsonify({'success': False, 'error': f'Item id {item_id} not found'}), 404
            if lot_number:
                return jsonify({'success': False, 'error': f'Item {upc} not found in lot {lot_number}'}), 404
            return jsonify({'success': False, 'error': f'Item {upc} not found'}), 404

        target_id = target_row[0]
        target_upc = ss_normalization._strip_leading_zeros_numeric(ss_normalization._normalize_upc(target_row[1])) or upc
        target_lot = ss_normalization._normalize_lot_number(target_row[2])
        if item_id is not None:
            if upc and target_upc and upc.lower() != target_upc.lower():
                return jsonify({'success': False, 'error': 'Provided upc does not match item id'}), 400
            if lot_number and target_lot and lot_number.lower() != target_lot.lower():
                return jsonify({'success': False, 'error': 'Provided lot_number does not match item id'}), 400
        prev_fb = target_row[3:]
        prev_fb_listed = int(prev_fb[0] or 0) if prev_fb else 0
        prev_fb_qty = int(prev_fb[1] or 1) if prev_fb else 1
        prev_fb_date = prev_fb[2] if prev_fb else None

        # Update marketplace-specific column and timestamp
        if marketplace == 'amazon':
            if listed:
                cur.execute('UPDATE bol_items SET listed_amazon=1, listed_amazon_date=datetime("now"), listed_amazon_source=? WHERE id = ?', (source, target_id))
                print(f"[list_status] Set listed_amazon=1 for {target_upc}")
            else:
                cur.execute('UPDATE bol_items SET listed_amazon=0, listed_amazon_date=NULL, listed_amazon_source=NULL WHERE id = ?', (target_id,))
                print(f"[list_status] Set listed_amazon=0 for {target_upc}")
        elif marketplace == 'ebay':
            if listed:
                cur.execute('UPDATE bol_items SET listed_ebay=1, listed_ebay_date=datetime("now"), listed_ebay_source=? WHERE id = ?', (source, target_id))
            else:
                cur.execute('UPDATE bol_items SET listed_ebay=0, listed_ebay_date=NULL, listed_ebay_source=NULL WHERE id = ?', (target_id,))
        elif marketplace == 'facebook':
            if listed:
                cur.execute('UPDATE bol_items SET listed_facebook=1, listed_facebook_date=datetime("now"), listed_facebook_qty=?, listed_facebook_source=? WHERE id = ?', (quantity, source, target_id))
                # Track in fbstore.db
                ss_facebook._track_fb_listing(target_upc, quantity, listed=True)
                action = 'manual_add' if prev_fb_listed == 0 else ('manual_qty_change' if prev_fb_qty != quantity else 'manual_add')
                ss_facebook._log_fb_listing_action(
                    action=action,
                    upc=target_upc,
                    delta_qty=(quantity - prev_fb_qty),
                    prev_qty=prev_fb_qty,
                    new_qty=quantity,
                    prev_listed=prev_fb_listed,
                    prev_listed_date=prev_fb_date,
                    note='list_status api'
                )
            else:
                cur.execute('UPDATE bol_items SET listed_facebook=0, listed_facebook_date=NULL, listed_facebook_qty=NULL, listed_facebook_source=NULL WHERE id = ?', (target_id,))
                # Mark as unlisted in fbstore.db
                ss_facebook._track_fb_listing(target_upc, quantity, listed=False)
                ss_facebook._log_fb_listing_action(
                    action='manual_unlist',
                    upc=target_upc,
                    delta_qty=(0 - prev_fb_qty),
                    prev_qty=prev_fb_qty,
                    new_qty=0,
                    prev_listed=prev_fb_listed,
                    prev_listed_date=prev_fb_date,
                    note='list_status api'
                )
        
        # Update legacy list_status column for backward compatibility
        # Item is "listed" if listed on ANY marketplace
        cur.execute('''
            UPDATE bol_items 
            SET list_status = CASE 
                WHEN COALESCE(listed_amazon, 0) = 1 OR COALESCE(listed_ebay, 0) = 1 OR COALESCE(listed_facebook, 0) = 1 
                THEN 'listed' 
                ELSE NULL 
            END
            WHERE id = ?
        ''', (target_id,))
        
        conn.commit()
        updated = 1

        # Best-effort listing audit trail for manual/system status toggles.
        try:
            ss_listing_log._listinglog_add_entry(
                upc=target_upc,
                platform=marketplace,
                action='listed' if listed else 'unlisted',
                source=source,
                quantity=quantity if marketplace == 'facebook' else None,
                success=True,
                meta={'lot_number': target_lot, 'via': 'api_bol_items_set_list_status'}
            )
        except Exception:
            pass
        
        # Fetch the updated state to return it
        cur.execute('SELECT listed_amazon, listed_ebay, listed_facebook FROM bol_items WHERE id = ?', (target_id,))
        row = cur.fetchone()
        current_state = {
            'listed_amazon': row[0] if row else 0,
            'listed_ebay': row[1] if row else 0,
            'listed_facebook': row[2] if row else 0,
            'source': source,
            'source_label': ss_listing_lifecycle._listing_source_label(source)
        }
        
        
        print(f"[list_status] Updated {updated} rows, new state: {current_state}")
        
        # CRITICAL: Clear the cache for /api/bol_items to show new data immediately
        # The 1-year cache was preventing checkbox states from persisting
        ss_runtime.cache.clear()
        
        # Invalidate cache so changes are immediately visible
        ss_caching.update_data_version()
        return jsonify({'success': True, 'updated': updated, 'current_state': current_state, 'lot_number': target_lot})
    except Exception as e:
        print(f"[list_status] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def get_bol_upcs():
    """Return a JSON list of all UPCs from bol.db bol_items table (UPC column)."""
    conn = None
    try:
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('SELECT upc FROM bol_items WHERE upc IS NOT NULL AND upc != ""')
        upcs = [row[0] for row in cur.fetchall()]
        return jsonify({'upcs': upcs})
    except Exception as e:
        return jsonify({'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()
