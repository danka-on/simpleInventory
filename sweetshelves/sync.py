"""Sync for Sweet Shelves."""

import datetime
import hashlib
import os
import sqlite3
import sys
import threading
import time
from flask import jsonify, request
from . import (
    caching as ss_caching, ebay_orders as ss_ebay_orders, errors as ss_errors, integrations as
    ss_integrations, normalization as ss_normalization, shipping_orders as ss_shipping_orders,
)


def get_sync_status():
    """Get last sync timestamps and current settings"""
    conn = None
    try:
        conn = sqlite3.connect('sync_settings.db')
        cur = conn.cursor()
        
        # Create table if not exists
        cur.execute('''CREATE TABLE IF NOT EXISTS sync_status (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
        
        # Get all sync timestamps
        cur.execute('SELECT key, value FROM sync_status')
        rows = cur.fetchall()
        status = dict(rows)
        
        
        return jsonify({
            'ebay_orders': status.get('ebay_orders_last'),
            'ebay_listings': status.get('ebay_listings_last'),
            'amazon_orders': status.get('amazon_orders_last'),
            'amazon_listings': status.get('amazon_listings_last'),
            'amazon_upcs': status.get('amazon_upcs_last'),
            'mail_center_refresh': status.get('mail_center_refresh_last'),
            'auto_sync_enabled': status.get('auto_sync_enabled') == 'true',
            'sync_interval': int(status.get('sync_interval', 60)),
            'orders_lookback': int(status.get('orders_lookback', 30)),
            'auto_upc_enabled': status.get('auto_upc_enabled', 'true') == 'true'
        })
    except Exception as e:
        print(f"❌ Error getting sync status: {e}")
        return jsonify({'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def save_sync_settings():
    """Save sync settings"""
    conn = None
    try:
        data = request.json
        conn = sqlite3.connect('sync_settings.db')
        cur = conn.cursor()
        
        # Create table if not exists
        cur.execute('''CREATE TABLE IF NOT EXISTS sync_status (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
        
        # Save settings
        settings = {
            'auto_sync_enabled': 'true' if data.get('auto_sync_enabled') else 'false',
            'sync_interval': str(data.get('sync_interval', 60)),
            'orders_lookback': str(data.get('orders_lookback', 30)),
            'auto_upc_enabled': 'true' if data.get('auto_upc_enabled') else 'false'
        }
        
        for key, value in settings.items():
            cur.execute('INSERT OR REPLACE INTO sync_status (key, value) VALUES (?, ?)', (key, value))
        
        conn.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"❌ Error saving settings: {e}")
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def update_sync_timestamp(key):
    """Helper function to update last sync timestamp"""
    conn = None
    try:
        conn = sqlite3.connect('sync_settings.db')
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS sync_status (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
        
        from datetime import datetime
        timestamp = datetime.now().astimezone().isoformat()
        cur.execute('INSERT OR REPLACE INTO sync_status (key, value) VALUES (?, ?)', (f'{key}_last', timestamp))
        
        conn.commit()
    except Exception as e:
        print(f"⚠️ Error updating sync timestamp: {e}")
    finally:
        if conn is not None:
            conn.close()


def _sync_status_parse_timestamp(raw_value):
    raw = str(raw_value or '').strip()
    if not raw:
        return None
    try:
        normalized = raw.replace('Z', '+00:00') if raw.endswith('Z') else raw
        dt = datetime.datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            local_tz = datetime.datetime.now().astimezone().tzinfo or datetime.timezone.utc
            return dt.replace(tzinfo=local_tz).astimezone(datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def _sync_manager_overdue_alert():
    sync_targets = [
        ('ebay_orders_last', 'eBay Orders'),
        ('ebay_listings_last', 'eBay Listings'),
        ('amazon_orders_last', 'Amazon Orders'),
        ('amazon_listings_last', 'Amazon Listings'),
        ('amazon_upcs_last', 'Amazon UPCs')
    ]
    conn = None
    try:
        conn = sqlite3.connect('sync_settings.db')
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS sync_status (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
        cur.execute('SELECT key, value FROM sync_status')
        status = dict(cur.fetchall() or [])
    except Exception:
        return None
    finally:
        if conn is not None:
            conn.close()

    if str(status.get('auto_sync_enabled', 'false')).strip().lower() != 'true':
        return None

    sync_interval = max(1, ss_normalization._coerce_int(status.get('sync_interval', 60), 60))
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if str(status.get('auto_upc_enabled', 'true')).strip().lower() != 'true':
        sync_targets = [item for item in sync_targets if item[0] != 'amazon_upcs_last']

    last_auto_sync_dt = _sync_status_parse_timestamp(status.get('last_auto_sync'))
    if last_auto_sync_dt is not None:
        last_auto_minutes = max(0, int((now_utc - last_auto_sync_dt).total_seconds() // 60))
        if last_auto_minutes <= sync_interval:
            return None

    freshest_minutes = None
    overdue_items = []

    for key, label in sync_targets:
        raw_last = status.get(key)
        last_dt = _sync_status_parse_timestamp(raw_last)
        if last_dt is None:
            continue

        minutes_since = max(0, int((now_utc - last_dt).total_seconds() // 60))
        if freshest_minutes is None or minutes_since < freshest_minutes:
            freshest_minutes = minutes_since
        if minutes_since > sync_interval:
            overdue_items.append({
                'key': key,
                'label': label,
                'last_sync': last_dt.isoformat(),
                'minutes_since': minutes_since,
                'never_synced': False
            })

    if not overdue_items:
        return None

    if freshest_minutes is not None and freshest_minutes <= sync_interval:
        return None

    snapshot_parts = [
        str(sync_interval),
        '|'.join(
            f"{item['key']}:{item['last_sync']}"
            for item in overdue_items
        )
    ]
    snapshot_hash = hashlib.md5(
        ('sync_overdue:' + '::'.join(snapshot_parts)).encode('utf-8')
    ).hexdigest()

    worst_minutes = 0
    for item in overdue_items:
        if item['minutes_since'] is not None:
            worst_minutes = max(worst_minutes, int(item['minutes_since']))

    return {
        'alert_type': 'sync_overdue',
        'severity': 'meltdown',
        'note': 'Contact Daniel',
        'message': 'Sync Manager is overdue. Contact Daniel.',
        'sync_interval_minutes': sync_interval,
        'overdue_count': len(overdue_items),
        'worst_minutes_since': worst_minutes,
        'overdue_items': overdue_items,
        'hash': snapshot_hash
    }


def sync_all():
    """Sync everything - eBay and Amazon orders, listings, and UPCs"""
    try:
        results = {}
        
        # Sync eBay orders
        try:
            # Use the local eBay orders sync function defined in this file
            ss_ebay_orders.orders()
            update_sync_timestamp('ebay_orders')
            results['ebay_orders'] = 'success'
        except Exception as e:
            print(f"⚠️ eBay orders sync failed: {e}")
            results['ebay_orders'] = str(e)
        
        # Sync eBay listings (searchRack)
        try:
            from DBmanager import enrich_searchrack_db
            enrich_searchrack_db(batch_size=500, do_backup=False)  # Enrich with eBay, BOL, and Amazon
            update_sync_timestamp('ebay_listings')
            results['ebay_listings'] = 'success'
        except Exception as e:
            print(f"⚠️ eBay listings sync failed: {e}")
            results['ebay_listings'] = str(e)
        
        # Sync Amazon orders
        if ss_integrations.AMAZON_AVAILABLE:
            try:
                amazon = ss_integrations.AmazonManager()
                amazon.sync_orders_to_db(days_back=30)
                
                # Enrich with images from amazonStore.db
                from DBmanager import enrich_amazon_sold_images
                enrich_amazon_sold_images()
                
                from DBmanager import process_sold_orders_inventory_reduction
                process_sold_orders_inventory_reduction()
                update_sync_timestamp('amazon_orders')
                results['amazon_orders'] = 'success'
            except Exception as e:
                print(f"⚠️ Amazon orders sync failed: {e}")
                results['amazon_orders'] = str(e)
            
            # Sync Amazon listings
            try:
                amazon = ss_integrations.AmazonManager()
                amazon.sync_listings_to_db()
                update_sync_timestamp('amazon_listings')
                results['amazon_listings'] = 'success'
            except Exception as e:
                print(f"⚠️ Amazon listings sync failed: {e}")
                results['amazon_listings'] = str(e)
            
            # Sync Amazon UPCs (only new items)
            try:
                count = sync_missing_upcs()
                update_sync_timestamp('amazon_upcs')
                results['amazon_upcs'] = f'success ({count} UPCs fetched)'
            except Exception as e:
                print(f"⚠️ Amazon UPC sync failed: {e}")
                results['amazon_upcs'] = str(e)
        
        success_count = sum(1 for v in results.values() if 'success' in v)
        total_count = len(results)
        ss_caching._listing_helper_scan_cache_clear()
        ss_shipping_orders._invalidate_ready_to_ship_cache()
        return jsonify({
            'success': True,
            'message': f'Sync completed: {success_count}/{total_count} successful',
            'details': results
        })
        
    except Exception as e:
        print(f"❌ Error in sync all: {e}")
        return jsonify({'success': False, 'message': ss_errors._safe_error(e, 'sync all')}), 500


def sync_ebay_orders_api():
    """Sync eBay orders"""
    try:
        # Use the local eBay orders sync function defined in this file
        ss_ebay_orders.orders()
        
        # Sync eBay seller fees
        try:
            from ebay_manager import EbayManager
            em = EbayManager()
            fees_count = em.sync_fees_to_db(days_back=90)
            print(f"✅ Synced fees for {fees_count} eBay orders")
        except Exception as e:
            print(f"⚠️ Error syncing eBay fees: {e}")
        
        # Sync eBay returns
        try:
            from ebay_manager import EbayManager
            em = EbayManager()
            returns_count = em.sync_returns_to_db(days_back=90)
            print(f"✅ Synced {returns_count} eBay returns")
        except Exception as e:
            print(f"⚠️ Error syncing eBay returns: {e}")
        
        # Auto-enrich recent orders with LOT numbers
        try:
            from lot_matcher import enrich_new_orders
            enrich_result = enrich_new_orders(days_back=7)
            if enrich_result.get('success'):
                print(f"✅ Enriched {enrich_result.get('enriched', 0)} eBay orders with LOT numbers")
        except Exception as e:
            print(f"⚠️ Error enriching eBay orders with LOTs: {e}")
        
        update_sync_timestamp('ebay_orders')
        ss_caching._listing_helper_scan_cache_clear()
        ss_shipping_orders._invalidate_ready_to_ship_cache()
        return jsonify({'success': True, 'message': 'eBay orders synced successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': ss_errors._safe_error(e, 'ebay orders sync')}), 500


def sync_ebay_listings_api():
    """Sync eBay listings and enrich with Amazon data"""
    try:
        from DBmanager import enrich_searchrack_db
        enrich_searchrack_db(batch_size=500, do_backup=False)  # Enrich with eBay, BOL, and Amazon data
        update_sync_timestamp('ebay_listings')
        ss_caching._listing_helper_scan_cache_clear()
        return jsonify({'success': True, 'message': 'eBay listings synced and enriched successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': ss_errors._safe_error(e, 'ebay listings sync')}), 500


def sync_amazon_orders_api():
    """Sync Amazon orders"""
    if not ss_integrations.AMAZON_AVAILABLE:
        return jsonify({'success': False, 'message': 'Amazon integration not available'}), 500
    try:
        amazon = ss_integrations.AmazonManager()
        amazon.sync_orders_to_db(days_back=30)
        
        # Sync returns
        try:
            returns_count = amazon.sync_returns_to_db(days_back=90)
            print(f"✅ Synced {returns_count} Amazon returns")
        except Exception as e:
            print(f"⚠️ Error syncing Amazon returns: {e}")
        
        # Enrich with images from amazonStore.db
        from DBmanager import enrich_amazon_sold_images
        enrich_amazon_sold_images()
        
        # ADDITIONAL: Enrich barcodes for orders that don't have them yet
        amazon_conn = None
        sold_conn = None
        try:
            sold_conn = sqlite3.connect('sold.db')
            sold_cur = sold_conn.cursor()
            
            amazon_conn = sqlite3.connect('amazonStore.db')
            amazon_conn.row_factory = sqlite3.Row
            amazon_cur = amazon_conn.cursor()
            
            # Find Amazon orders without barcodes
            sold_cur.execute('''
                SELECT id, item_id FROM orders 
                WHERE store = 'amazon' 
                AND (barcode IS NULL OR barcode = '' OR barcode = item_id)
                AND item_id IS NOT NULL
            ''')
            
            orders_to_enrich = sold_cur.fetchall()
            enriched = 0
            
            for order_id, asin in orders_to_enrich:
                # Look up UPC in amazonStore.db
                amazon_cur.execute('SELECT UPC FROM ITEMS WHERE ASIN = ? COLLATE NOCASE', (asin,))
                row = amazon_cur.fetchone()
                
                if row and row['UPC'] and row['UPC'] != asin:
                    # Update sold order with proper UPC
                    sold_cur.execute('UPDATE orders SET barcode = ? WHERE id = ?', (row['UPC'], order_id))
                    enriched += 1
            
            sold_conn.commit()
            
            print(f"✅ Enriched {enriched} Amazon orders with barcodes")
            
        except Exception as e:
            print(f"⚠️ Error enriching Amazon order barcodes: {e}")
        finally:
            if sold_conn is not None:
                sold_conn.close()
            if amazon_conn is not None:
                amazon_conn.close()
        
        # Auto-enrich recent orders with LOT numbers
        try:
            from lot_matcher import enrich_new_orders
            enrich_result = enrich_new_orders(days_back=7)
            if enrich_result.get('success'):
                print(f"✅ Enriched {enrich_result.get('enriched', 0)} Amazon orders with LOT numbers")
        except Exception as e:
            print(f"⚠️ Error enriching Amazon orders with LOTs: {e}")
        
        from DBmanager import process_sold_orders_inventory_reduction
        process_sold_orders_inventory_reduction()
        update_sync_timestamp('amazon_orders')
        ss_caching._listing_helper_scan_cache_clear()
        ss_shipping_orders._invalidate_ready_to_ship_cache()
        return jsonify({'success': True, 'message': 'Amazon orders synced successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': ss_errors._safe_error(e, 'amazon orders sync')}), 500


def sync_amazon_listings_api():
    """Sync Amazon listings with quota protection"""
    if not ss_integrations.AMAZON_AVAILABLE:
        return jsonify({'success': False, 'message': 'Amazon integration not available'}), 500
    try:
        amazon = ss_integrations.AmazonManager()
        result = amazon.sync_listings_to_db()
        
        # Handle quota exceeded
        if result == -1:
            return jsonify({
                'success': False, 
                'message': 'Amazon listings sync quota exceeded - please wait 24 hours',
                'quota_exceeded': True
            }), 429  # 429 Too Many Requests
        
        # Handle no changes
        if result == 0:
            return jsonify({
                'success': True, 
                'message': 'No listings to sync (last sync was recent)'
            })
        
        update_sync_timestamp('amazon_listings')
        ss_caching._listing_helper_scan_cache_clear()
        return jsonify({
            'success': True, 
            'message': f'Amazon listings synced successfully ({result} items)'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': ss_errors._safe_error(e, 'amazon listings sync')}), 500


def sync_missing_upcs(max_items=25):
    """
    Sync UPCs for Amazon items that don't have them (where UPC = ASIN)
    
    Args:
        max_items: Maximum number of items to process per run (default 25 to avoid quota issues)
    
    Returns:
        Number of UPCs found
    """
    import time
    from datetime import datetime, timedelta

    conn = None
    retry_minutes = 60

    # Get retry interval from sync settings (use sync_interval in minutes)
    # Default to 60 minutes if not set
    settings_conn = None
    try:
        settings_conn = sqlite3.connect('sync_settings.db')
        settings_cur = settings_conn.cursor()
        settings_cur.execute('SELECT value FROM sync_status WHERE key = ?', ('sync_interval',))
        row = settings_cur.fetchone()
        if row:
            retry_minutes = int(row[0])
    except Exception:
        pass  # Use default if settings not available
    finally:
        if settings_conn:
            settings_conn.close()

    try:
        conn = sqlite3.connect('amazonStore.db')
        cur = conn.cursor()

        # Ensure tracking columns exist
        try:
            cur.execute('ALTER TABLE ITEMS ADD COLUMN upc_fetch_attempted INTEGER DEFAULT 0')
            conn.commit()
        except Exception:
            pass
        try:
            cur.execute('ALTER TABLE ITEMS ADD COLUMN upc_last_fetch_date TEXT')
            conn.commit()
        except Exception:
            pass

        # Find items that need UPC fetching using smart tracking:
        # - upc_fetch_attempted = 0 (never tried) OR
        # - upc_fetch_attempted = 2 AND last fetch > retry_interval ago (retry after error)
        # - EXCLUDE upc_fetch_attempted = 1 (no UPC exists, skip forever)
        # - EXCLUDE upc_fetch_attempted = 3 (successfully fetched)
        retry_threshold = (datetime.now() - timedelta(minutes=retry_minutes)).isoformat()

        cur.execute('''
            SELECT ASIN FROM ITEMS 
            WHERE (UPC = ASIN OR UPC IS NULL OR UPC = '')
            AND (
                upc_fetch_attempted = 0 
                OR upc_fetch_attempted IS NULL
                OR (upc_fetch_attempted = 2 AND (upc_last_fetch_date IS NULL OR upc_last_fetch_date < ?))
            )
            ORDER BY (upc_last_fetch_date IS NOT NULL), upc_last_fetch_date ASC
            LIMIT ?
        ''', (retry_threshold, max_items))
        items_without_upcs = [row[0] for row in cur.fetchall()]

        if not items_without_upcs:
            print("✅ No items need UPC fetching (using smart tracking)")
            return 0

        print(f"🔍 Found {len(items_without_upcs)} items without UPCs (limited to {max_items}). Fetching...")

        try:
            amazon = ss_integrations.AmazonManager()
            print("✅ AmazonManager initialized")
        except Exception as e:
            print(f"❌ Failed to initialize AmazonManager: {e}")
            raise

        upcs_found = 0
        items_processed = 0

        quota_exceeded_count = 0

        for i, asin in enumerate(items_without_upcs, 1):
            try:
                # Get catalog item
                catalog_data = amazon.get_catalog_item(asin)
                items_processed += 1

                if not catalog_data:
                    # API error - mark for retry based on sync interval (status 2)
                    cur.execute('''
                        UPDATE ITEMS 
                        SET upc_fetch_attempted = 2, 
                            upc_last_fetch_date = ? 
                        WHERE ASIN = ?
                    ''', (datetime.now().isoformat(), asin))
                    retry_text = f"{retry_minutes} minutes" if retry_minutes < 60 else f"{retry_minutes // 60} hours"
                    print(f"⚠️ [{i}/{len(items_without_upcs)}] {asin}: No catalog data (will retry in {retry_text})")
                    conn.commit()
                    time.sleep(1.0)  # Increased delay
                    continue

                upc = None

                # Check attributes section first (most common)
                if 'attributes' in catalog_data:
                    attrs = catalog_data['attributes']
                    if 'externally_assigned_product_identifier' in attrs:
                        for identifier in attrs['externally_assigned_product_identifier']:
                            if identifier.get('type') in ['upc', 'ean']:
                                upc = identifier.get('value')
                                break

                # Fallback to identifiers section
                if not upc and 'identifiers' in catalog_data:
                    identifiers = catalog_data['identifiers']
                    if isinstance(identifiers, list):
                        for id_group in identifiers:
                            if 'identifiers' in id_group:
                                for identifier in id_group['identifiers']:
                                    if identifier.get('identifierType') in ['UPC', 'EAN']:
                                        upc = identifier.get('identifier')
                                        break

                if upc:
                    # UPC found - mark as successfully fetched (status 3)
                    cur.execute('''
                        UPDATE ITEMS 
                        SET UPC = ?, 
                            upc_fetch_attempted = 3, 
                            upc_last_fetch_date = ? 
                        WHERE ASIN = ?
                    ''', (upc, datetime.now().isoformat(), asin))
                    upcs_found += 1
                    print(f"✅ [{i}/{len(items_without_upcs)}] {asin}: {upc}")
                else:
                    # No UPC exists in catalog - mark to skip forever (status 1)
                    cur.execute('''
                        UPDATE ITEMS 
                        SET upc_fetch_attempted = 1, 
                            upc_last_fetch_date = ? 
                        WHERE ASIN = ?
                    ''', (datetime.now().isoformat(), asin))
                    print(f"⚠️ [{i}/{len(items_without_upcs)}] {asin}: No UPC found (marked to skip)")

                # Commit after each item to preserve progress
                conn.commit()

                # Rate limiting: Catalog Items API allows 2 requests per second
                # Use 1 second delay to avoid quota exhaustion (1 per second is safer)
                time.sleep(1.0)

            except Exception as e:
                # Check if quota exceeded
                error_str = str(e)
                if 'QuotaExceeded' in error_str or 'quota' in error_str.lower():
                    quota_exceeded_count += 1
                    print(f"⚠️ [{i}/{len(items_without_upcs)}] {asin}: Quota exceeded, stopping UPC sync")
                    print(f"💡 {upcs_found} UPCs fetched before hitting quota. Will retry remaining items in {retry_minutes} minutes.")
                    break  # Stop processing to avoid further quota violations

                # API error - mark for retry based on sync interval (status 2)
                cur.execute('''
                    UPDATE ITEMS 
                    SET upc_fetch_attempted = 2, 
                        upc_last_fetch_date = ? 
                    WHERE ASIN = ?
                ''', (datetime.now().isoformat(), asin))
                conn.commit()
                retry_text = f"{retry_minutes} minutes" if retry_minutes < 60 else f"{retry_minutes // 60} hours"
                print(f"❌ [{i}/{len(items_without_upcs)}] {asin}: Error - {e} (will retry in {retry_text})")
                time.sleep(1.0)  # Increased delay

        if quota_exceeded_count > 0:
            print(f"\n⚠️ UPC sync stopped: Amazon API quota exceeded")
            print(f"✅ Progress saved: {upcs_found} UPCs found from {items_processed} items checked")
            print(f"💡 Remaining items will be retried in {retry_minutes} minutes")
        else:
            print(f"\n✅ UPC sync complete: {upcs_found} UPCs found from {items_processed} items checked")

        return upcs_found
    except Exception as e:
        print(f"❌ Error in sync_missing_upcs: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        if conn:
            conn.close()


def sync_amazon_upcs_api():
    """Sync UPCs for Amazon items without barcodes"""
    if not ss_integrations.AMAZON_AVAILABLE:
        return jsonify({'success': False, 'message': 'Amazon integration not available'}), 500
    try:
        print("🔄 Starting Amazon UPC sync...")
        count = sync_missing_upcs()
        print(f"✅ Amazon UPC sync completed: {count} UPCs fetched")
        update_sync_timestamp('amazon_upcs')
        ss_caching._listing_helper_scan_cache_clear()
        
        if count == 0:
            message = 'No items need UPC fetching - all items already have UPCs'
        else:
            message = f'Fetched {count} UPCs for items without barcodes'
        
        return jsonify({'success': True, 'message': message, 'count': count})
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"❌ Amazon UPC sync error: {e}")
        print(error_details)
        return jsonify({'success': False, 'message': ss_errors._safe_error(e, 'amazon UPC sync')}), 500


def recover_corrupted_upcs_api():
    """One-time bulk recovery for corrupted UPCs (UPC = ASIN but status = 3)"""
    if not ss_integrations.AMAZON_AVAILABLE:
        return jsonify({'success': False, 'message': 'Amazon integration not available'}), 500
    conn = None
    try:
        print("🔍 Scanning for corrupted UPCs...")
        
        conn = sqlite3.connect('amazonStore.db')
        cur = conn.cursor()
        
        # Find all items where UPC = ASIN but upc_fetch_attempted = 3 (corrupted)
        cur.execute('''
            SELECT ASIN, UPC, upc_fetch_attempted 
            FROM ITEMS 
            WHERE UPC = ASIN AND upc_fetch_attempted = 3
        ''')
        corrupted_items = cur.fetchall()
        
        if not corrupted_items:
            return jsonify({
                'success': True, 
                'message': 'No corrupted UPCs found - all items are healthy',
                'count': 0
            })
        
        print(f"📋 Found {len(corrupted_items)} corrupted UPCs:")
        for item in corrupted_items[:5]:  # Show first 5
            print(f"   - ASIN: {item[0]}, UPC: {item[1]}")
        if len(corrupted_items) > 5:
            print(f"   ... and {len(corrupted_items) - 5} more")
        
        # Reset tracking for all corrupted items
        cur.execute('''
            UPDATE ITEMS 
            SET upc_fetch_attempted = 0, upc_last_fetch_date = NULL
            WHERE UPC = ASIN AND upc_fetch_attempted = 3
        ''')
        conn.commit()
        reset_count = cur.rowcount
        
        print(f"✅ Reset tracking for {reset_count} corrupted UPCs")
        
        return jsonify({
            'success': True,
            'message': f'Reset {reset_count} corrupted UPCs - run "Fetch Missing Amazon UPCs" to re-fetch them',
            'count': reset_count
        })
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"❌ UPC recovery error: {e}")
        print(error_details)
        return jsonify({'success': False, 'message': ss_errors._safe_error(e, 'UPC recovery')}), 500
    finally:
        if conn is not None:
            conn.close()


def fix_amazon_barcodes():
    """One-time fix: Enrich Amazon orders in sold.db with barcodes and images from amazonStore.db"""
    amazon_conn = None
    sold_conn = None
    try:
        sold_conn = sqlite3.connect('sold.db')
        sold_cur = sold_conn.cursor()
        
        amazon_conn = sqlite3.connect('amazonStore.db')
        amazon_conn.row_factory = sqlite3.Row
        amazon_cur = amazon_conn.cursor()
        
        # Find ALL Amazon orders without proper barcodes (where barcode = ASIN or null)
        sold_cur.execute('''
            SELECT id, item_id, barcode, image FROM orders 
            WHERE store = 'amazon' 
            AND item_id IS NOT NULL
        ''')
        
        all_amazon_orders = sold_cur.fetchall()
        fixed_barcodes = 0
        fixed_images = 0
        
        print(f"🔍 Scanning {len(all_amazon_orders)} Amazon orders...")
        
        for order_id, asin, current_barcode, current_image in all_amazon_orders:
            # Look up in amazonStore.db
            amazon_cur.execute('SELECT UPC, IMAGE FROM ITEMS WHERE ASIN = ? COLLATE NOCASE', (asin,))
            row = amazon_cur.fetchone()
            
            if row:
                needs_update = False
                updates = []
                params = []
                
                # Fix barcode if missing or equals ASIN
                if row['UPC'] and row['UPC'] != asin and (not current_barcode or current_barcode == asin):
                    updates.append('barcode = ?')
                    params.append(row['UPC'])
                    fixed_barcodes += 1
                    needs_update = True
                
                # Fix image if missing
                if row['IMAGE'] and not current_image:
                    updates.append('image = ?')
                    params.append(row['IMAGE'])
                    fixed_images += 1
                    needs_update = True
                
                if needs_update:
                    params.append(order_id)
                    sold_cur.execute(f"UPDATE orders SET {', '.join(updates)} WHERE id = ?", params)
        
        sold_conn.commit()
        
        print(f"✅ Fixed {fixed_barcodes} barcodes and {fixed_images} images")
        ss_shipping_orders._invalidate_ready_to_ship_cache()
        
        return jsonify({
            'success': True,
            'message': f'Fixed {fixed_barcodes} barcodes and {fixed_images} images for Amazon orders',
            'fixed_barcodes': fixed_barcodes,
            'fixed_images': fixed_images
        })
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"❌ Fix Amazon barcodes error: {e}")
        print(error_details)
        return jsonify({'success': False, 'message': ss_errors._safe_error(e, 'fix amazon barcodes')}), 500
    finally:
        if sold_conn is not None:
            sold_conn.close()
        if amazon_conn is not None:
            amazon_conn.close()


@ss_errors.require_debug_mode
def sync_debug():
      return jsonify({
        'amazon_available': ss_integrations.AMAZON_AVAILABLE,
        'bol_available': ss_integrations.BOL_AVAILABLE,
        'python_version': sys.version,
        'working_directory': os.getcwd()
    })


def auto_sync_worker():
    """Background thread that runs auto-sync based on settings"""
    print("🔄 Auto-sync worker started")
    
    while True:
        conn = None
        try:
            # Check if auto-sync is enabled
            conn = sqlite3.connect('sync_settings.db')
            cur = conn.cursor()
            
            # Create table if not exists
            cur.execute('''CREATE TABLE IF NOT EXISTS sync_status (
                key TEXT PRIMARY KEY,
                value TEXT
            )''')
            
            cur.execute('SELECT value FROM sync_status WHERE key = ?', ('auto_sync_enabled',))
            row = cur.fetchone()
            auto_sync_enabled = row and row[0] == 'true'
            
            if not auto_sync_enabled:
                # Check every 60 seconds if auto-sync is enabled
                time.sleep(60)
                continue
            
            # Get sync interval (in minutes)
            cur.execute('SELECT value FROM sync_status WHERE key = ?', ('sync_interval',))
            row = cur.fetchone()
            sync_interval = int(row[0]) if row else 60
            
            # Get last sync time
            cur.execute('SELECT value FROM sync_status WHERE key = ?', ('last_auto_sync',))
            row = cur.fetchone()
            last_sync = row[0] if row else None
            
            
            # Check if it's time to sync
            from datetime import datetime, timedelta
            now = datetime.now()
            should_sync = False
            
            if not last_sync:
                should_sync = True
                print(f"🔄 Auto-sync: First sync (never synced before)")
            else:
                last_sync_dt = datetime.fromisoformat(last_sync)
                time_since_sync = (now - last_sync_dt).total_seconds() / 60  # minutes
                
                if time_since_sync >= sync_interval:
                    should_sync = True
                    print(f"🔄 Auto-sync: {time_since_sync:.1f} minutes since last sync (interval: {sync_interval})")
            
            if should_sync:
                print(f"🔄 Starting auto-sync at {now.strftime('%Y-%m-%d %H:%M:%S')}")
                
                # Sync eBay orders
                try:
                    print("  📦 Syncing eBay orders...")
                    ss_ebay_orders.orders()
                    update_sync_timestamp('ebay_orders')
                    print("  ✅ eBay orders synced")
                except Exception as e:
                    print(f"  ⚠️ eBay orders sync failed: {e}")
                
                # Sync eBay listings (searchRack)
                try:
                    print("  📋 Syncing eBay listings...")
                    from DBmanager import enrich_searchrack_db
                    enrich_searchrack_db(batch_size=500, do_backup=False)
                    update_sync_timestamp('ebay_listings')
                    print("  ✅ eBay listings synced")
                except Exception as e:
                    print(f"  ⚠️ eBay listings sync failed: {e}")
                
                # Sync Amazon if available
                if ss_integrations.AMAZON_AVAILABLE:
                    # Get orders lookback setting
                    conn = sqlite3.connect('sync_settings.db')
                    try:
                        cur = conn.cursor()
                        cur.execute('SELECT value FROM sync_status WHERE key = ?', ('orders_lookback',))
                        row = cur.fetchone()
                        orders_lookback = int(row[0]) if row else 30
                    finally:
                        conn.close()
                    
                    # CHANGED ORDER: Sync listings FIRST so amazonStore.db has latest data
                    try:
                        print("  � Syncing Amazon listings...")
                        amazon = ss_integrations.AmazonManager()
                        amazon.sync_listings_to_db()
                        update_sync_timestamp('amazon_listings')
                        print("  ✅ Amazon listings synced")
                    except Exception as e:
                        print(f"  ⚠️ Amazon listings sync failed: {e}")
                    
                    # Check if auto UPC is enabled
                    conn = sqlite3.connect('sync_settings.db')
                    try:
                        cur = conn.cursor()
                        cur.execute('SELECT value FROM sync_status WHERE key = ?', ('auto_upc_enabled',))
                        row = cur.fetchone()
                        auto_upc = row and row[0] == 'true'
                    finally:
                        conn.close()
                    
                    # MOVED UP: Fetch UPCs BEFORE orders so they're available for enrichment
                    if auto_upc:
                        try:
                            print("  � Fetching Amazon UPCs...")
                            # Call the correct function with max_items limit (reduced to 25 to avoid quota issues)
                            upcs_found = sync_missing_upcs(max_items=25)
                            update_sync_timestamp('amazon_upcs')
                            print(f"  ✅ Amazon UPCs fetched: {upcs_found} found")
                        except Exception as e:
                            print(f"  ⚠️ Amazon UPCs fetch failed: {e}")
                    
                    # NOW sync orders LAST (UPCs and listings are already in amazonStore.db)
                    try:
                        print("  📦 Syncing Amazon orders...")
                        amazon = ss_integrations.AmazonManager()
                        amazon.sync_orders_to_db(days_back=orders_lookback)
                        
                        from DBmanager import enrich_amazon_sold_images
                        enrich_amazon_sold_images()
                        
                        # ADDED: Enrich barcodes for orders that don't have them yet
                        amazon_conn = None
                        sold_conn = None
                        try:
                            sold_conn = sqlite3.connect('sold.db')
                            sold_cur = sold_conn.cursor()
                            
                            amazon_conn = sqlite3.connect('amazonStore.db')
                            amazon_conn.row_factory = sqlite3.Row
                            amazon_cur = amazon_conn.cursor()
                            
                            # Find Amazon orders without barcodes
                            sold_cur.execute('''
                                SELECT id, item_id FROM orders 
                                WHERE store = 'amazon' 
                                AND (barcode IS NULL OR barcode = '' OR barcode = item_id)
                                AND item_id IS NOT NULL
                            ''')
                            
                            orders_to_enrich = sold_cur.fetchall()
                            enriched = 0
                            
                            for order_id, asin in orders_to_enrich:
                                # Look up UPC in amazonStore.db
                                amazon_cur.execute('SELECT UPC FROM ITEMS WHERE ASIN = ? COLLATE NOCASE', (asin,))
                                row = amazon_cur.fetchone()
                                
                                if row and row['UPC'] and row['UPC'] != asin:
                                    # Update sold order with proper UPC
                                    sold_cur.execute('UPDATE orders SET barcode = ? WHERE id = ?', (row['UPC'], order_id))
                                    enriched += 1
                            
                            sold_conn.commit()
                            
                            if enriched > 0:
                                print(f"  ✅ Enriched {enriched} Amazon orders with barcodes")
                            
                        except Exception as e:
                            print(f"  ⚠️ Error enriching Amazon order barcodes: {e}")
                        finally:
                            if sold_conn is not None:
                                sold_conn.close()
                            if amazon_conn is not None:
                                amazon_conn.close()
                        
                        from DBmanager import process_sold_orders_inventory_reduction
                        process_sold_orders_inventory_reduction()
                        update_sync_timestamp('amazon_orders')
                        print("  ✅ Amazon orders synced")
                    except Exception as e:
                        print(f"  ⚠️ Amazon orders sync failed: {e}")
                    
                    # Sync Amazon payouts/settlements
                    try:
                        print("  💵 Syncing Amazon payouts...")
                        amazon = ss_integrations.AmazonManager()
                        payout_count = amazon.sync_settlements_to_db(days_back=90)
                        update_sync_timestamp('amazon_payouts')
                        print(f"  ✅ Amazon payouts synced: {payout_count} settlements")
                    except Exception as e:
                        print(f"  ⚠️ Amazon payouts sync failed: {e}")
                
                # Sync eBay payouts
                try:
                    print("  💵 Syncing eBay payouts...")
                    from ebay_manager import EbayManager
                    ebay = EbayManager()
                    payout_count = ebay.sync_payouts_to_db(days_back=90)
                    update_sync_timestamp('ebay_payouts')
                    print(f"  ✅ eBay payouts synced: {payout_count} payouts")
                except Exception as e:
                    print(f"  ⚠️ eBay payouts sync failed: {e}")

                ss_shipping_orders._invalidate_ready_to_ship_cache()
                
                # Update last auto-sync timestamp
                conn = sqlite3.connect('sync_settings.db')
                try:
                    cur = conn.cursor()
                    cur.execute('INSERT OR REPLACE INTO sync_status (key, value) VALUES (?, ?)', 
                               ('last_auto_sync', now.isoformat()))
                    conn.commit()
                finally:
                    conn.close()
                
                print(f"✅ Auto-sync completed at {now.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # Sleep for 1 minute before checking again
            time.sleep(60)
            
        except Exception as e:
            print(f"❌ Auto-sync worker error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(60)  # Wait before retrying
        finally:
            if conn is not None:
                conn.close()


def _start_auto_sync_thread():
    """Start the auto-sync background thread"""
    auto_sync_thread = threading.Thread(target=auto_sync_worker, daemon=True)
    auto_sync_thread.start()
    print("🚀 Auto-sync thread started")
