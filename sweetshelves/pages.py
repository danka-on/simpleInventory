"""Pages for Sweet Shelves."""

import sqlite3
from DBmanager import createSearchRackDB
from flask import jsonify, make_response, redirect, render_template, request
from inventory import find_item
from . import (
    database as ss_database, errors as ss_errors, normalization as ss_normalization, prep_context as
    ss_prep_context, prep_schema as ss_prep_schema,
)


def tools():
    return render_template('tools.html')


def prep_media_cleaner_page():
    """Management cleaner for prep photos, audio, and video files."""
    return render_template('prep_media_cleaner.html')


def bol_stats_page():
    return render_template('bol_stats.html')


def barcode_print_que():
     return render_template('barcode_print_que.html')


def barcode_print_view():
    """Mobile-friendly print view for barcode queue"""
    return render_template('barcode_print_view.html')


def sync():
    return render_template('sync.html')


def misc():
    return render_template('misc.html')


def financial_analytics():
    print("DEBUG: Financial analytics page accessed")
    return render_template('financial_analytics.html')


def inventory_seller_analytics():
    """Decision-oriented warehouse, sales velocity, and return-risk analytics."""
    return render_template('inventory_analytics.html')


def inventory_cleanup_decisions():
    """Strategic cleanup queue for aging active inventory."""
    return render_template('inventory_cleanup.html')


def shelfcreator():
    # Legacy route; redirect to Shelf Manager
    return redirect('/shelfmanager')


def shelfmanager():
    return render_template('shelfmanager.html')


def extractor():
    return render_template('extractor.html')


def items_to_list_page():
    return render_template('items_to_list.html')


def price_master_page():
    """Bulk re-pricing dashboard for eBay + Amazon store listings."""
    return render_template('price_master.html')


def ready_to_ship_page():
    """Ready to Ship orders page."""
    response = make_response(render_template('ready_to_ship.html'))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def ready_to_ship_prep_summaries():
    try:
        data = request.get_json(silent=True) or {}
        raw_barcodes = data.get('barcodes') or []
        if not isinstance(raw_barcodes, list):
            return jsonify({'success': False, 'error': 'barcodes must be an array'}), 400

        normalized = []
        seen = set()
        for raw in raw_barcodes:
            upc = ss_normalization._normalize_upc_preserve_suffix_for_match(raw)
            if not upc:
                continue
            key = upc.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(upc)

        if not normalized:
            return jsonify({'success': True, 'summaries': {}})

        ss_prep_schema._ensure_items_prep_tables()
        with ss_database.db_connection('bol.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            payload = {}
            for upc in normalized:
                context = ss_prep_context._build_ready_to_ship_prep_context(cur, upc, include_details=False)
                payload[upc] = context.get('summary') or {
                    'has_alerts': False,
                    'entry_count': 0,
                    'note_count': 0,
                    'defect_count': 0,
                    'image_count': 0,
                    'latest_at': '',
                    'has_suffixed': False
                }
        return jsonify({'success': True, 'summaries': payload})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'ready_to_ship:prep_summaries')}), 500


def ready_to_ship_prep_context(barcode):
    try:
        upc = ss_normalization._normalize_upc_preserve_suffix_for_match(barcode)
        if not upc:
            return jsonify({'success': False, 'error': 'Missing barcode'}), 400
        ss_prep_schema._ensure_items_prep_tables()
        with ss_database.db_connection('bol.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            context = ss_prep_context._build_ready_to_ship_prep_context(cur, upc, include_details=True)
        context['success'] = True
        return jsonify(context)
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'ready_to_ship:prep_context')}), 500


def alerts_page():
    """Alert center page (currently no-warehouse listing alerts)."""
    return render_template('alerts.html')


def store_manager_page():
    """Central hub for store-related tools."""
    return render_template('store_manager.html')


def mail_center_page():
    """Centralized mailbox for store communications."""
    return render_template('mail_center.html')


def label_master_page():
    """Label Master portal for buying/printing labels for sold orders."""
    return render_template('label.html')


def fb_listings_page():
    return render_template('fb_listings.html')


def store_listing_helper_page():
    return render_template('store_listing_helper.html')


def privacy():
    return "<h1>Privacy Policy</h1><p>No user data is stored or shared. This app is for sandbox testing only.</p>"


def search():
    query = request.form["query"]
    result = find_item(query)
    return render_template("index.html", search_result=result)


def refresh_searchrack():
    from DBmanager import enrich_searchrack_db
    createSearchRackDB()
    enrich_searchrack_db(batch_size=500, do_backup=False)  # Enrich with all sources including Amazon
    return 'SearchRack database updated and enriched! <a href="/searchrack">Back to Search</a>'


def test_sold_item():
    import datetime
    conn = None
    try:
        today = datetime.date.today().isoformat()
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        cur.execute("UPDATE orders SET isHandled = 0, paid_time = ?, shipped_time = ? WHERE id = 99", (today, today))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()
