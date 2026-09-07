"""Sales for Sweet Shelves."""

import sqlite3
from DBmanager import ensure_sold_orders_schema
from flask import jsonify, request
from . import (
    amazon_listing as ss_amazon_listing, config as ss_config, ebay_orders as ss_ebay_orders, errors as
    ss_errors, integrations as ss_integrations, listing_alerts as ss_listing_alerts, normalization as
    ss_normalization, runtime as ss_runtime, shipping_identity as ss_shipping_identity, shipping_orders
    as ss_shipping_orders, warehouse_matching as ss_warehouse_matching,
)


def get_sold_orders_route():
    print("we're getting sold orders")
    try:
        data = request.get_json(silent=True) if request.is_json else {}
        if not isinstance(data, dict):
            data = {}
        days = data.get('days', 90)
        
        # Sync eBay orders
        ss_ebay_orders.get_ebay_orders(days=days)
        
        # Also sync Amazon orders if available
        if ss_integrations.AMAZON_AVAILABLE:
            try:
                print(f"🔄 Syncing Amazon orders (last {days} days)...")
                amazon = ss_integrations.AmazonManager()
                amazon.sync_orders_to_db(days_back=days)
            except Exception as e:
                print(f"⚠️ Error syncing Amazon orders: {e}")
                # Don't fail the whole request if Amazon sync fails
        
        # Process inventory reduction after fetching sold orders
        from DBmanager import process_sold_orders_inventory_reduction
        process_sold_orders_inventory_reduction()
        ss_shipping_orders._invalidate_ready_to_ship_cache()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


@ss_runtime.cache.cached(timeout=300, query_string=True)  # Cache for 5 minutes based on query params (days parameter)
def sold_orders():
    days = int(request.args.get('days', 1))
    if days < 1:
        days = 1
    elif days > 120:
        days = 120
    
    # Get orders from sold.db
    sold_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'sold.db'), timeout=30.0)
    prepared_orders = []
    rawbol_conn = None
    try:
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        ensure_sold_orders_schema(sold_cur, sold_conn, default_store='ebay')
        ss_shipping_identity._ensure_ready_to_ship_notes_table(sold_cur)
        # Only show orders from the last N days.
        # Keep ordering deterministic for duplicate suppression and UI stability.
        sold_cur.execute('''
            SELECT *
            FROM orders
            WHERE paid_time >= date('now', '-' || ? || ' days')
            ORDER BY paid_time DESC, id DESC
        ''', (days,))
        raw_orders = sold_cur.fetchall()

        # Defensive dedupe: intermittent sync races can leave clone rows.
        # Keep the newest row per strong content signature.
        dedupe_seen = set()
        orders = []
        duplicate_count = 0
        for order in raw_orders:
            try:
                qty_val = int(order['quantity']) if order['quantity'] is not None else 0
            except Exception:
                qty_val = 0
            try:
                price_val = float(order['price']) if order['price'] is not None else 0.0
            except Exception:
                price_val = 0.0
            signature = (
                str(order['store'] or '').strip().lower(),
                str(order['order_id'] or '').strip(),
                str(order['item_id'] or '').strip(),
                ss_normalization._normalize_upc(ss_warehouse_matching._effective_sold_order_barcode(order) or ''),
                str(order['title'] or '').strip().lower(),
                qty_val,
                round(price_val, 4),
                str(order['paid_time'] or '').strip(),
                str(order['shipped_time'] or '').strip(),
                str(order['shipping_name'] or '').strip().lower()
            )
            if signature in dedupe_seen:
                duplicate_count += 1
                continue
            dedupe_seen.add(signature)
            orders.append(order)

        if duplicate_count > 0:
            print(f"[sold_orders] Deduped {duplicate_count} transient duplicate row(s) from sold.db query")

        order_row_ids = [order['id'] for order in orders]
        note_lookup = ss_shipping_identity._load_ready_to_ship_note_lookup(sold_cur, order_row_ids)
        label_lookup = ss_shipping_identity._load_ready_to_ship_label_lookup(sold_cur, order_row_ids)
        finder_match_lookup = ss_shipping_identity._load_order_finder_match_lookup(sold_cur, order_row_ids)
        amazon_listing_lookup = {}
        amazon_store_conn = None
        try:
            amazon_store_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'amazonStore.db'))
            amazon_store_conn.row_factory = sqlite3.Row
            amazon_cols = {str(row[1]).lower() for row in amazon_store_conn.execute('PRAGMA table_info(ITEMS)').fetchall()}
            condition_note_expr = 'CONDITION_NOTE' if 'condition_note' in amazon_cols else "'' AS CONDITION_NOTE"
            image_expr = 'IMAGE' if 'image' in amazon_cols else "'' AS IMAGE"
            upc_expr = 'UPC' if 'upc' in amazon_cols else "'' AS UPC"
            for listing in amazon_store_conn.execute(f'SELECT ASIN, SKU, {upc_expr}, CONDITION, {condition_note_expr}, {image_expr} FROM ITEMS'):
                payload = {
                    'condition': str(listing['CONDITION'] or '').strip(),
                    'condition_description': str(listing['CONDITION_NOTE'] or '').strip(),
                    'image': ss_shipping_identity._ready_to_ship_image_value(listing['IMAGE'])
                }
                for key in (listing['ASIN'], listing['SKU'], listing['UPC']):
                    normalized_key = str(key or '').strip().lower()
                    if normalized_key:
                        amazon_listing_lookup[normalized_key] = payload
        except Exception as e:
            print(f"Warning: Could not load Amazon condition data for Ready to Ship: {e}")
        finally:
            if amazon_store_conn is not None:
                amazon_store_conn.close()
        ebay_listing_lookup = {}
        ebay_store_conn = None
        try:
            ebay_store_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'ebayStore.db'))
            ebay_store_conn.row_factory = sqlite3.Row
            ebay_cols = {str(row[1]).lower() for row in ebay_store_conn.execute('PRAGMA table_info(INVENTORY)').fetchall()}
            if 'condition' in ebay_cols or 'description' in ebay_cols or 'conditiondescription' in ebay_cols or 'image' in ebay_cols:
                condition_expr = 'Condition' if 'condition' in ebay_cols else "'' AS Condition"
                description_expr = 'Description' if 'description' in ebay_cols else "'' AS Description"
                condition_description_expr = 'ConditionDescription' if 'conditiondescription' in ebay_cols else "'' AS ConditionDescription"
                image_expr = 'Image' if 'image' in ebay_cols else "'' AS Image"
                upc_expr = 'UPC' if 'upc' in ebay_cols else "'' AS UPC"
                for listing in ebay_store_conn.execute(f'SELECT ItemID, SKU, {upc_expr}, {condition_expr}, {description_expr}, {condition_description_expr}, {image_expr} FROM INVENTORY'):
                    payload = {
                        'condition': str(listing['Condition'] or '').strip(),
                        'description': str(listing['Description'] or '').strip(),
                        'condition_description': str(listing['ConditionDescription'] or '').strip(),
                        'image': ss_shipping_identity._ready_to_ship_image_value(listing['Image'])
                    }
                    for key in (listing['ItemID'], listing['SKU'], listing['UPC']):
                        normalized_key = str(key or '').strip().lower()
                        if normalized_key:
                            ebay_listing_lookup[normalized_key] = payload
        except Exception as e:
            print(f"Warning: Could not load eBay condition/description data for Ready to Ship: {e}")
        finally:
            if ebay_store_conn is not None:
                ebay_store_conn.close()
        rawbol_cur = None
        image_backfills = 0
        try:
            rawbol_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'rawbol.db'))
            rawbol_cur = rawbol_conn.cursor()
            rawbol_cur.execute('SELECT 1 FROM raw_bol_items LIMIT 1')
        except Exception as e:
            print(f"Warning: Could not open Raw BOL image fallback for Ready to Ship: {e}")
            if rawbol_conn is not None:
                rawbol_conn.close()
            rawbol_conn = None
            rawbol_cur = None
        for order in orders:
            order_dict = dict(order)
            stored_image = ss_shipping_identity._ready_to_ship_image_value(order_dict.get('image'))
            if str(order_dict.get('store') or '').strip().lower() == 'amazon':
                for lookup_value in (order_dict.get('listing_asin'), order_dict.get('item_id'), order_dict.get('sku')):
                    listing = amazon_listing_lookup.get(str(lookup_value or '').strip().lower())
                    if not listing:
                        continue
                    if not str(order_dict.get('item_condition') or '').strip() and listing.get('condition'):
                        order_dict['item_condition'] = listing['condition']
                    if not str(order_dict.get('item_condition_description') or '').strip() and listing.get('condition_description'):
                        order_dict['item_condition_description'] = listing['condition_description']
                    if not ss_shipping_identity._ready_to_ship_image_value(order_dict.get('image')) and listing.get('image'):
                        order_dict['image'] = listing['image']
                    break
            if not ss_shipping_identity._ready_to_ship_image_value(order_dict.get('image')):
                image_lookup_values = (
                    order_dict.get('item_id'),
                    order_dict.get('sku'),
                    order_dict.get('barcode'),
                    order_dict.get('source_upc'),
                    order_dict.get('source_base_upc')
                )
                image_lookup_keys = []
                for lookup_value in image_lookup_values:
                    direct_key = str(lookup_value or '').strip().lower()
                    if direct_key and direct_key not in image_lookup_keys:
                        image_lookup_keys.append(direct_key)
                    for variant in ss_warehouse_matching._sold_removal_barcode_variants(lookup_value):
                        variant_key = str(variant or '').strip().lower()
                        if variant_key and variant_key not in image_lookup_keys:
                            image_lookup_keys.append(variant_key)
                for listing_lookup in (amazon_listing_lookup, ebay_listing_lookup):
                    for lookup_key in image_lookup_keys:
                        listing = listing_lookup.get(lookup_key)
                        if listing and listing.get('image'):
                            order_dict['image'] = listing['image']
                            break
                    if ss_shipping_identity._ready_to_ship_image_value(order_dict.get('image')):
                        break
            if str(order_dict.get('store') or '').strip().lower() == 'ebay':
                for lookup_value in (order_dict.get('listing_listing_id'), order_dict.get('item_id'), order_dict.get('sku')):
                    listing = ebay_listing_lookup.get(str(lookup_value or '').strip().lower())
                    if not listing:
                        continue
                    if not str(order_dict.get('item_condition') or '').strip() and listing.get('condition'):
                        order_dict['item_condition'] = listing['condition']
                    if not str(order_dict.get('item_description') or '').strip() and listing.get('description'):
                        order_dict['item_description'] = listing['description']
                    if not str(order_dict.get('item_condition_description') or '').strip() and listing.get('condition_description'):
                        order_dict['item_condition_description'] = listing['condition_description']
                    if not ss_shipping_identity._ready_to_ship_image_value(order_dict.get('image')) and listing.get('image'):
                        order_dict['image'] = listing['image']
                    break
            if not ss_shipping_identity._ready_to_ship_image_value(order_dict.get('image')) and rawbol_cur is not None:
                fallback_image = ss_shipping_identity._ready_to_ship_rawbol_image(rawbol_cur, (
                    order_dict.get('barcode'),
                    order_dict.get('source_upc'),
                    order_dict.get('source_base_upc'),
                    order_dict.get('sku')
                ))
                if fallback_image:
                    order_dict['image'] = fallback_image
            repaired_image = ss_shipping_identity._ready_to_ship_image_value(order_dict.get('image'))
            if not stored_image and repaired_image:
                sold_cur.execute('''
                    UPDATE orders
                    SET image = ?
                    WHERE id = ?
                      AND LOWER(TRIM(COALESCE(image, ''))) IN (
                          '', 'nan', 'none', 'null', 'undefined', 'n/a', 'na'
                      )
                ''', (repaired_image, order_dict.get('id')))
                image_backfills += sold_cur.rowcount
            if str(order_dict.get('store') or '').strip().lower() == 'amazon':
                raw_condition = str(order_dict.get('item_condition') or '').strip()
                if raw_condition:
                    order_dict['item_condition'] = ss_amazon_listing._amazon_normalize_condition_type(raw_condition, raw_condition)
            stored_location = str(order_dict.get('location') or '').strip()
            order_dict['stored_location'] = stored_location
            order_dict['location_locked'] = bool(stored_location)
            order_dict['finder_searchrack_id'] = ''
            order_dict['finder_matched_barcode'] = ''
            order_dict['finder_matched_location'] = ''
            try:
                finder_match = finder_match_lookup.get(int(order['id']))
            except Exception:
                finder_match = None
            if finder_match:
                order_dict['finder_searchrack_id'] = ss_normalization._coerce_int(finder_match['searchrack_id'], 0) or ''
                order_dict['finder_matched_barcode'] = str(finder_match['barcode'] or '').strip()
                order_dict['finder_matched_location'] = str(finder_match['location'] or '').strip()
            ss_shipping_identity._apply_ready_to_ship_note_payload(
                order_dict,
                note_lookup.get(int(order['id'])),
                label_lookup.get(int(order['id']), [])
            )
            prepared_orders.append(order_dict)
        if rawbol_conn is not None:
            rawbol_conn.close()
            rawbol_conn = None
        if image_backfills:
            sold_conn.commit()
            print(f"[sold_orders] Repaired {image_backfills} missing image(s) from marketplace/Raw BOL data")
    finally:
        if rawbol_conn is not None:
            rawbol_conn.close()
        sold_conn.close()
    
    # Enrich with location from searchRack.db
    result = []
    rack_conn = None
    try:
        rack_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        rack_conn.row_factory = sqlite3.Row
        rack_cur = rack_conn.cursor()
        listing_inventory_match_lookup = ss_listing_alerts._load_listing_inventory_match_lookup()
        
        for order_dict in prepared_orders:
            effective_barcode = ss_warehouse_matching._effective_sold_order_barcode(order_dict, prefer_manual_override=True)
            if effective_barcode:
                order_dict['stored_barcode'] = str(order_dict.get('barcode') or '').strip()
                order_dict['barcode'] = effective_barcode

            finder_searchrack_id = ss_normalization._coerce_int(order_dict.get('finder_searchrack_id'), 0)
            if finder_searchrack_id:
                try:
                    finder_match = ss_warehouse_matching._ready_to_ship_searchrack_match_by_id(rack_cur, finder_searchrack_id)
                    if finder_match:
                        if not str(order_dict.get('finder_matched_barcode') or '').strip():
                            order_dict['finder_matched_barcode'] = finder_match.get('barcode') or order_dict.get('barcode') or ''
                        if not str(order_dict.get('finder_matched_location') or '').strip():
                            order_dict['finder_matched_location'] = ss_warehouse_matching._ready_to_ship_match_display_location(finder_match)
                        if finder_match.get('warehouse_note'):
                            order_dict['warehouse_note'] = finder_match.get('warehouse_note') or ''
                        if not str(order_dict.get('stored_location') or '').strip() and order_dict.get('finder_matched_location'):
                            order_dict['stored_location'] = order_dict['finder_matched_location']
                            order_dict['location'] = order_dict['finder_matched_location']
                            order_dict['location_locked'] = True
                except Exception as e:
                    print(f"Warning: Failed to load stored Finder warehouse row for order {order_dict.get('id')}: {e}")

            if order_dict.get('location_locked') and not ss_normalization._coerce_int(order_dict.get('finder_searchrack_id'), 0):
                try:
                    resolved_match = ss_warehouse_matching._ready_to_ship_resolve_searchrack_location_match(
                        rack_cur,
                        order_dict.get('barcode') or order_dict.get('stored_barcode') or '',
                        order_dict.get('stored_location') or order_dict.get('location') or '',
                        fallback_barcodes=[
                            order_dict.get('stored_barcode') or '',
                            order_dict.get('source_upc') or '',
                            order_dict.get('sku') or ''
                        ]
                    )
                    if resolved_match:
                        order_dict['finder_searchrack_id'] = resolved_match.get('id') or ''
                        order_dict['finder_matched_barcode'] = resolved_match.get('barcode') or order_dict.get('barcode') or ''
                        if resolved_match.get('warehouse_note'):
                            order_dict['warehouse_note'] = resolved_match.get('warehouse_note') or ''
                        order_dict['finder_matched_location'] = (
                            ss_warehouse_matching._ready_to_ship_match_display_location(resolved_match)
                            or order_dict.get('stored_location')
                            or ''
                        )
                except Exception as e:
                    print(f"Warning: Failed to resolve Finder warehouse match for order {order_dict.get('id')}: {e}")
            
            # If location is empty and barcode exists, look it up in searchRack
            if (not order_dict.get('location') or order_dict.get('location', '').strip() == '') and order_dict.get('barcode'):
                try:
                    rack_schema = ss_warehouse_matching._searchrack_removal_schema(rack_cur)
                    exact_rack_rows = ss_warehouse_matching._searchrack_matches_for_barcode(
                        rack_cur, order_dict['barcode'], schema=rack_schema, include_zero=False
                    )
                    suffix_rack_rows = ss_warehouse_matching._ready_to_ship_suffix_inventory_matches(
                        rack_cur, order_dict['barcode'], schema=rack_schema
                    )
                    sold_barcode_key = ss_warehouse_matching._sold_removal_barcode_key(order_dict['barcode'])
                    sold_barcode_has_suffix = '-' in sold_barcode_key
                    condition_is_new = ss_warehouse_matching._ready_to_ship_condition_is_new(order_dict)
                    suggestion_match = False

                    if not condition_is_new and not sold_barcode_has_suffix and suffix_rack_rows:
                        rack_rows = suffix_rack_rows
                        suggestion_match = True
                    else:
                        rack_rows = exact_rack_rows

                    if not rack_rows and not suffix_rack_rows:
                        base_barcode = ss_shipping_identity._barcode_base_without_suffix(order_dict['barcode'])
                        if base_barcode and ss_normalization._normalize_upc_preserve_suffix_for_match(base_barcode) != ss_normalization._normalize_upc_preserve_suffix_for_match(order_dict['barcode']):
                            rack_rows = ss_warehouse_matching._searchrack_matches_for_barcode(
                                rack_cur, base_barcode, schema=rack_schema, include_zero=False
                            )
                    if not rack_rows and suffix_rack_rows:
                        rack_rows = suffix_rack_rows
                        suggestion_match = True
                    
                    if rack_rows:
                        rack_rows = ss_warehouse_matching._ready_to_ship_rank_inventory_matches(rack_rows, order_dict)
                        preferred_match = rack_rows[0]
                        preferred_barcode = preferred_match.get('barcode') or order_dict.get('barcode') or ''
                        order_dict['location_match_suggested'] = bool(suggestion_match)
                        order_dict['location_match_barcode'] = preferred_barcode
                        order_dict['suffix_variations'] = ss_warehouse_matching._ready_to_ship_suffix_alternatives(
                            suffix_rack_rows, preferred_barcode
                        )
                        preferred_barcode_key = ss_warehouse_matching._sold_removal_barcode_key(preferred_barcode)
                        rack_rows = [
                            match for match in rack_rows
                            if ss_warehouse_matching._sold_removal_barcode_key(match.get('barcode')) == preferred_barcode_key
                        ]
                        order_dict['finder_searchrack_id'] = preferred_match.get('id') or ''
                        order_dict['finder_matched_barcode'] = preferred_barcode
                        order_dict['finder_matched_location'] = ss_warehouse_matching._ready_to_ship_match_display_location(preferred_match)
                        if preferred_match.get('warehouse_note'):
                            order_dict['warehouse_note'] = preferred_match.get('warehouse_note') or ''
                        # Collect all locations for this barcode
                        locations = []
                        for rack_row in rack_rows:
                            loc = rack_row.get('item_position') or rack_row.get('pictureposition')
                            if loc:
                                locations.append({
                                    'code': loc,
                                    'image': rack_row.get('pictureposition') if rack_row.get('pictureposition') and str(rack_row.get('pictureposition')).strip() else loc,
                                    'quantity': rack_row.get('quantity') if rack_row.get('quantity') else 1,
                                    'warehouse_note': rack_row.get('warehouse_note') or ''
                                })
                        
                        # Store as JSON array if multiple locations, or single string for backward compatibility
                        if len(locations) > 1:
                            order_dict['locations'] = locations  # Array of location objects
                            order_dict['location'] = locations[0]['code']  # First location for backward compatibility
                        elif len(locations) == 1:
                            order_dict['location'] = locations[0]['code']
                            order_dict['location_image'] = locations[0]['image']
                            order_dict['warehouse_note'] = locations[0].get('warehouse_note') or ''
                except sqlite3.Error as e:
                    # If searchRack query fails, just skip location lookup for this order
                    print(f"Warning: Failed to lookup location for barcode {order_dict['barcode']}: {e}")

            # Store-listing Finder mappings are intentionally a fallback: only
            # use one when the marketplace barcode produced no live location.
            if not str(order_dict.get('location') or '').strip():
                listing_mapping = ss_listing_alerts._listing_inventory_match_for_order(
                    order_dict, listing_inventory_match_lookup
                )
                if listing_mapping:
                    try:
                        mapped_match = ss_warehouse_matching._ready_to_ship_searchrack_match_by_id(
                            rack_cur, listing_mapping.get('searchrack_id')
                        )
                        if mapped_match and ss_normalization._coerce_int(mapped_match.get('quantity'), 0) > 0:
                            mapped_location = ss_warehouse_matching._ready_to_ship_match_display_location(mapped_match)
                            mapped_barcode = str(mapped_match.get('barcode') or '').strip()
                            if mapped_location and mapped_barcode:
                                order_dict['location'] = mapped_location
                                order_dict['location_image'] = (
                                    mapped_match.get('pictureposition') or mapped_location
                                )
                                order_dict['warehouse_note'] = mapped_match.get('warehouse_note') or ''
                                order_dict['finder_searchrack_id'] = mapped_match.get('id') or ''
                                order_dict['finder_matched_barcode'] = mapped_barcode
                                order_dict['finder_matched_location'] = mapped_location
                                order_dict['location_match_suggested'] = True
                                order_dict['location_match_barcode'] = mapped_barcode
                                order_dict['location_match_source'] = 'store_listing_fallback'
                                order_dict['listing_inventory_match_id'] = listing_mapping.get('id') or ''
                    except Exception as mapping_error:
                        print(
                            f"Warning: Store-listing fallback failed for sold order "
                            f"{order_dict.get('id')}: {mapping_error}"
                        )

            # If still no location, flag for manual search via Finder page
            if not order_dict.get('location') or order_dict.get('location', '').strip() == '':
                if order_dict.get('title'):
                    order_dict['location_search'] = order_dict['title']

            result.append(order_dict)
        
    except Exception as e:
        # If searchRack.db is unavailable, return orders without location enrichment
        print(f"Warning: searchRack.db unavailable, skipping location lookup: {e}")
        result = prepared_orders
    finally:
        try:
            if rack_conn is not None:
                rack_conn.close()
        except Exception:
            pass

    return jsonify(result)
