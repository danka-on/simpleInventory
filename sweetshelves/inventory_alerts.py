"""Inventory alerts for Sweet Shelves."""

import datetime
import json
import sqlite3
import threading
from flask import jsonify, request
from . import (
    email_settings as ss_email_settings, errors as ss_errors, health as ss_health, inventory_history as
    ss_inventory_history,
)


def send_inventory_alert_email():
    """Send inventory alert email for items with store quantity but no searchRack quantity"""
    try:
        data = request.json
        emails = data.get('emails', [])
        
        print(f"📧 Inventory alert request received for emails: {emails}")
        
        if not emails:
            print("❌ No emails provided")
            return jsonify({'success': False, 'error': 'No email addresses provided'}), 400
        
        # Collect inventory mismatches
        print("🔍 Collecting inventory mismatches...")
        inventory_issues = collect_inventory_mismatches()
        
        print(f"📊 Issues found - eBay: {len(inventory_issues['ebay_items'])}, Amazon: {len(inventory_issues['amazon_items'])}, Duplicates: {len(inventory_issues['duplicate_locations'])}, Has issues: {inventory_issues['has_issues']}")
        
        # Only send if there are eBay or Amazon issues (exclude duplicate locations from email)
        has_email_issues = len(inventory_issues['ebay_items']) > 0 or len(inventory_issues['amazon_items']) > 0
        if not has_email_issues:
            print("ℹ️ No inventory issues found - email not sent")
            return jsonify({
                'success': True,
                'message': 'No inventory issues found - email not sent'
            })
        
        # Generate HTML email
        print("📝 Generating email content...")
        html_body = generate_inventory_email_html(inventory_issues)
        plain_body = generate_inventory_email_plain(inventory_issues)
        
        # Send email
        subject = f"⚠️ Inventory Alert - {len(inventory_issues['ebay_items']) + len(inventory_issues['amazon_items'])} Items Need Attention - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        print(f"📤 Sending email with subject: {subject}")
        success, error = ss_email_settings.send_email_smtp(emails, subject, plain_body, html_body)
        
        if not success:
            print(f"❌ Email send failed: {error}")
            return jsonify({'success': False, 'error': error}), 500
        
        print(f"✅ Inventory alert sent to: {', '.join(emails)}")
        
        return jsonify({
            'success': True,
            'message': f'Inventory alert sent to {len(emails)} recipient(s)'
        })
    except Exception as e:
        print(f"❌ Exception in send_inventory_alert_email: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def collect_inventory_mismatches():
    """
    Find store listings with stock after their warehouse row reached a terminal history event.
    SEARCHRACK contains active rows only, so rack history supplies the former zero-row signal.
    Also checks for duplicate barcodes in different locations.
    """
    issues = {
        'has_issues': False,
        'ebay_items': [],
        'amazon_items': [],
        'duplicate_locations': [],
        'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    rack_conn = None
    
    try:
        # Build active warehouse identities first; any active row cancels an older terminal event.
        rack_conn = sqlite3.connect('searchRack.db')
        rack_conn.row_factory = sqlite3.Row
        rack_cur = rack_conn.cursor()
        
        # Get quantity column name
        rack_cur.execute('PRAGMA table_info(SEARCHRACK)')
        cols = [r[1] for r in rack_cur.fetchall()]
        qty_col = 'QUANTITY' if 'QUANTITY' in cols else ('QTY' if 'QTY' in cols else None)
        
        if not qty_col:
            return issues
        
        active_keys = set()
        rack_cur.execute(f'''
            SELECT BARCODE, ITEMID
            FROM SEARCHRACK
            WHERE COALESCE(CAST({qty_col} AS INTEGER), 0) > 0
        ''')
        for row in rack_cur.fetchall():
            for value in (row['BARCODE'], row['ITEMID']):
                key = str(value or '').strip().upper()
                if key:
                    active_keys.add(key)
        rack_conn.close()
        rack_conn = None

        ss_inventory_history._flush_searchrack_history_outbox()
        history_conn = sqlite3.connect('rackhistory.db')
        history_conn.row_factory = sqlite3.Row
        history_cur = history_conn.cursor()
        ss_inventory_history._ensure_removed_items_table(history_cur)
        history_cur.execute('''
            SELECT barcode, title, source_row_json
            FROM removed_items
            WHERE COALESCE(old_quantity, 0) > 0
              AND COALESCE(new_quantity, 0) <= 0
              AND (undone_at IS NULL OR undone_at = '')
              AND COALESCE(event_status, 'applied') = 'applied'
            ORDER BY id DESC
        ''')
        zero_qty_items = {}
        for row in history_cur.fetchall():
            try:
                snapshot = json.loads(row['source_row_json'] or '{}')
                if not isinstance(snapshot, dict):
                    snapshot = {}
            except Exception:
                snapshot = {}
            folded = ss_inventory_history._row_casefold_dict(snapshot)
            identities = {
                str(row['barcode'] or '').strip().upper(),
                str(folded.get('barcode') or '').strip().upper(),
                str(folded.get('itemid') or '').strip().upper(),
            }
            title = str(row['title'] or folded.get('title') or '').strip()
            for identity in identities:
                if identity and identity not in active_keys:
                    zero_qty_items.setdefault(identity, title)
        history_conn.close()
        
        # Check eBay store for matching items with quantity > 0 (only if we have zero-qty items)
        if zero_qty_items:
            ebay_conn = None
            try:
                ebay_conn = sqlite3.connect('ebayStore.db')
                ebay_conn.row_factory = sqlite3.Row
                ebay_cur = ebay_conn.cursor()
                
                ebay_cur.execute('''
                    SELECT SKU, Title, Quantity, ItemID 
                    FROM INVENTORY 
                    WHERE SKU IS NOT NULL 
                    AND TRIM(SKU) != ''
                    AND CAST(Quantity AS INTEGER) > 0
                ''')
                
                for row in ebay_cur.fetchall():
                    sku = row['SKU'].strip().upper() if row['SKU'] else ''
                    if sku and sku in zero_qty_items:
                        issues['ebay_items'].append({
                            'barcode': sku,
                            'title': row['Title'] or zero_qty_items[sku],
                            'store_qty': row['Quantity'],
                            'item_id': row['ItemID']
                        })
                        issues['has_issues'] = True
                
            except Exception as e:
                print(f"Error checking eBay inventory: {e}")
            finally:
                if ebay_conn is not None:
                    ebay_conn.close()
            
            # Check Amazon store for matching items with quantity > 0
            amazon_conn = None
            try:
                amazon_conn = sqlite3.connect('amazonStore.db')
                amazon_conn.row_factory = sqlite3.Row
                amazon_cur = amazon_conn.cursor()

                amazon_cur.execute('PRAGMA table_info(ITEMS)')
                amazon_cols = [r[1] for r in amazon_cur.fetchall()]
                amazon_qty_col = next((c for c in amazon_cols if str(c).upper() in ('QUANTITY', 'QTY')), None)

                if amazon_qty_col:
                    amazon_cur.execute(f'''
                        SELECT UPC, TITLE, {amazon_qty_col} AS STORE_QTY, ASIN
                        FROM ITEMS
                        WHERE UPC IS NOT NULL
                        AND TRIM(UPC) != ''
                        AND CAST(COALESCE({amazon_qty_col}, 0) AS INTEGER) > 0
                    ''')

                    for row in amazon_cur.fetchall():
                        upc = row['UPC'].strip().upper() if row['UPC'] else ''
                        if upc and upc in zero_qty_items:
                            issues['amazon_items'].append({
                                'barcode': upc,
                                'title': row['TITLE'] or zero_qty_items[upc],
                                'store_qty': row['STORE_QTY'],
                                'asin': row['ASIN']
                            })
                            issues['has_issues'] = True
                
            except Exception as e:
                print(f"Error checking Amazon inventory: {e}")
            finally:
                if amazon_conn is not None:
                    amazon_conn.close()
        
        # Check for duplicate barcodes (non-suffixed) in different locations
        try:
            rack_conn = sqlite3.connect('searchRack.db')
            rack_conn.row_factory = sqlite3.Row
            rack_cur = rack_conn.cursor()
            
            # Get quantity column name
            rack_cur.execute('PRAGMA table_info(SEARCHRACK)')
            cols = [r[1] for r in rack_cur.fetchall()]
            qty_col = 'QUANTITY' if 'QUANTITY' in cols else ('QTY' if 'QTY' in cols else None)
            
            if qty_col:
                # Find barcodes that appear in multiple different locations (excluding suffixed barcodes)
                rack_cur.execute(f'''
                    SELECT 
                        BARCODE,
                        TITLE,
                        GROUP_CONCAT(ITEM_POSITION || ' (Qty: ' || {qty_col} || ')', ', ') as locations,
                        COUNT(DISTINCT ITEM_POSITION) as location_count,
                        SUM({qty_col}) as total_qty
                    FROM SEARCHRACK
                    WHERE BARCODE IS NOT NULL 
                    AND TRIM(BARCODE) != ''
                    AND BARCODE NOT LIKE '%-%'
                    AND ITEM_POSITION IS NOT NULL
                    AND TRIM(ITEM_POSITION) != ''
                    AND COALESCE(CAST({qty_col} AS INTEGER), 0) > 0
                    GROUP BY BARCODE
                    HAVING COUNT(DISTINCT ITEM_POSITION) > 1
                    ORDER BY location_count DESC, BARCODE
                ''')
                
                for row in rack_cur.fetchall():
                    issues['duplicate_locations'].append({
                        'barcode': row['BARCODE'],
                        'title': row['TITLE'] or 'Unknown',
                        'locations': row['locations'],
                        'location_count': row['location_count'],
                        'total_qty': row['total_qty'] or 0
                    })
                    issues['has_issues'] = True
            
        except Exception as e:
            print(f"Error checking for duplicate locations: {e}")
        finally:
            if rack_conn is not None:
                rack_conn.close()
        
    except Exception as e:
        print(f"Error collecting inventory mismatches: {e}")
    finally:
        if rack_conn is not None:
            rack_conn.close()
    
    return issues


def generate_inventory_email_html(issues):
    """Generate HTML email body for inventory alert"""
    ebay_rows = ""
    for item in issues['ebay_items']:
        ebay_rows += f"""
        <tr>
            <td style="padding: 12px; border-bottom: 1px solid #e0e0e0;">{item['barcode']}</td>
            <td style="padding: 12px; border-bottom: 1px solid #e0e0e0;">{item['title'][:60]}...</td>
            <td style="padding: 12px; border-bottom: 1px solid #e0e0e0; text-align: center; font-weight: bold; color: #e74c3c;">{item['store_qty']}</td>
        </tr>
        """
    
    amazon_rows = ""
    for item in issues['amazon_items']:
        amazon_rows += f"""
        <tr>
            <td style="padding: 12px; border-bottom: 1px solid #e0e0e0;">{item['barcode']}</td>
            <td style="padding: 12px; border-bottom: 1px solid #e0e0e0;">{item['title'][:60]}...</td>
            <td style="padding: 12px; border-bottom: 1px solid #e0e0e0; text-align: center; font-weight: bold; color: #e74c3c;">{item['store_qty']}</td>
        </tr>
        """
    
    total_issues = len(issues['ebay_items']) + len(issues['amazon_items'])
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f9f9f9; margin: 0; padding: 20px; }}
            .container {{ max-width: 900px; margin: 0 auto; background: white; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
            .header {{ background: linear-gradient(135deg, #e74c3c, #c0392b); color: white; padding: 30px; border-radius: 12px 12px 0 0; }}
            .header h1 {{ margin: 0; font-size: 28px; }}
            .header p {{ margin: 10px 0 0 0; opacity: 0.9; }}
            .content {{ padding: 30px; }}
            .section {{ margin-bottom: 30px; }}
            .section h2 {{ color: #2c3e50; font-size: 20px; margin: 0 0 15px 0; padding-bottom: 10px; border-bottom: 2px solid #e0e0e0; }}
            table {{ width: 100%; border-collapse: collapse; background: white; }}
            th {{ background: #34495e; color: white; padding: 12px; text-align: left; font-weight: 600; }}
            .alert {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 20px 0; border-radius: 4px; }}
            .footer {{ padding: 20px; text-align: center; color: #7f8c8d; font-size: 14px; background: #ecf0f1; border-radius: 0 0 12px 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚠️ Inventory Alert</h1>
                <p>{total_issues} issue(s) detected in inventory</p>
                <p style="font-size: 14px; margin-top: 10px;">{issues['timestamp']}</p>
            </div>
            
            <div class="content">
                <div class="alert">
                    <strong>⚠️ Action Required:</strong> Issues detected with your inventory. Please review and address the following items.
                </div>
    """
    
    if issues['ebay_items']:
        html += f"""
                <div class="section">
                    <h2>🛒 eBay ({len(issues['ebay_items'])} items)</h2>
                    <p style="color: #7f8c8d; margin-bottom: 15px;">Listed online but showing 0 in physical inventory:</p>
                    <table>
                        <thead>
                            <tr>
                                <th>Barcode (SKU)</th>
                                <th>Title</th>
                                <th style="text-align: center;">Store Qty</th>
                            </tr>
                        </thead>
                        <tbody>
                            {ebay_rows}
                        </tbody>
                    </table>
                </div>
        """
    
    if issues['amazon_items']:
        html += f"""
                <div class="section">
                    <h2>📦 Amazon ({len(issues['amazon_items'])} items)</h2>
                    <p style="color: #7f8c8d; margin-bottom: 15px;">Listed online but showing 0 in physical inventory:</p>
                    <table>
                        <thead>
                            <tr>
                                <th>Barcode (UPC)</th>
                                <th>Title</th>
                                <th style="text-align: center;">Store Qty</th>
                            </tr>
                        </thead>
                        <tbody>
                            {amazon_rows}
                        </tbody>
                    </table>
                </div>
        """
    
    html += """
            </div>
            
            <div class="footer">
                <p>This is an automated inventory alert from your Store App</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html


def generate_inventory_email_plain(issues):
    """Generate plain text email body for inventory alert"""
    total_issues = len(issues['ebay_items']) + len(issues['amazon_items'])
    
    text = f"""
⚠️ INVENTORY ALERT
Generated: {issues['timestamp']}

{total_issues} issue(s) detected in inventory.
Action required: Please review and address the following items.

"""
    
    if issues['ebay_items']:
        text += f"\n🛒 eBay ({len(issues['ebay_items'])} items):\n"
        text += "Listed online but showing 0 in physical inventory\n"
        text += "-" * 70 + "\n"
        for item in issues['ebay_items']:
            text += f"Barcode: {item['barcode']}\n"
            text += f"Title: {item['title'][:60]}\n"
            text += f"Store Qty: {item['store_qty']}\n"
            text += "-" * 70 + "\n"
    
    if issues['amazon_items']:
        text += f"\n📦 Amazon ({len(issues['amazon_items'])} items):\n"
        text += "Listed online but showing 0 in physical inventory\n"
        text += "-" * 70 + "\n"
        for item in issues['amazon_items']:
            text += f"Barcode: {item['barcode']}\n"
            text += f"Title: {item['title'][:60]}\n"
            text += f"Store Qty: {item['store_qty']}\n"
            text += "-" * 70 + "\n"
    
    text += """
--
This is an automated inventory alert from your Store App.
"""
    
    return text


def emailer_alert_worker():
    """Background worker to send email alerts"""
    import time
    while True:
        conn = None
        try:
            # Check every 5 minutes
            time.sleep(300)  # 5 minutes
            
            # Get emailer settings
            conn = sqlite3.connect('searchRack.db')
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            
            # Ensure table exists
            cur.execute('''
                CREATE TABLE IF NOT EXISTS emailer_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT,
                    alert_type TEXT DEFAULT 'test',
                    interval TEXT DEFAULT '1d',
                    last_test_sent TEXT,
                    UNIQUE(email, alert_type)
                )
            ''')
            
            # Get all active alert settings
            cur.execute('SELECT email, alert_type, interval, last_test_sent FROM emailer_settings')
            settings = cur.fetchall()
            
            if not settings:
                continue
            
            from datetime import datetime, timedelta
            now = datetime.now()
            
            for setting in settings:
                email = setting['email']
                alert_type = setting['alert_type']
                interval = setting['interval']
                last_sent = setting['last_test_sent']
                
                # Only process inventory and health alerts in this worker (skip test)
                if alert_type not in ['inventory', 'health']:
                    continue
                
                # Parse interval to minutes
                interval_minutes = 0
                if interval == '30m':
                    interval_minutes = 30
                elif interval == '10m':
                    interval_minutes = 10
                elif interval == '1h':
                    interval_minutes = 60
                elif interval == '12h':
                    interval_minutes = 720
                elif interval == '1d':
                    interval_minutes = 1440
                elif interval == '1w':
                    interval_minutes = 10080
                elif interval == '1mo':
                    interval_minutes = 43200
                else:
                    continue
                
                # Check if it's time to send
                should_send = False
                if not last_sent:
                    should_send = True
                else:
                    try:
                        last_sent_dt = datetime.fromisoformat(last_sent)
                        time_since_sent = (now - last_sent_dt).total_seconds() / 60  # minutes
                        if time_since_sent >= interval_minutes:
                            should_send = True
                    except Exception:
                        should_send = True
                
                if should_send:
                    try:
                        if alert_type == 'inventory':
                            # Collect inventory issues
                            inventory_issues = collect_inventory_mismatches()
                            
                            # Only send if there are eBay or Amazon issues (exclude duplicate locations)
                            has_email_issues = len(inventory_issues['ebay_items']) > 0 or len(inventory_issues['amazon_items']) > 0
                            if has_email_issues:
                                html_body = generate_inventory_email_html(inventory_issues)
                                plain_body = generate_inventory_email_plain(inventory_issues)
                                
                                subject = f"⚠️ Inventory Alert - {len(inventory_issues['ebay_items']) + len(inventory_issues['amazon_items'])} Items Need Attention - {now.strftime('%Y-%m-%d %H:%M')}"
                                success, error = ss_email_settings.send_email_smtp([email], subject, plain_body, html_body)
                                
                                if success:
                                    print(f"✅ Inventory alert sent to: {email}")
                                    
                                    # Update last sent time
                                    conn = sqlite3.connect('searchRack.db')
                                    try:
                                        cur = conn.cursor()
                                        cur.execute('''
                                        UPDATE emailer_settings 
                                        SET last_test_sent = ? 
                                        WHERE email = ? AND alert_type = ?
                                    ''', (now.isoformat(), email, alert_type))
                                        conn.commit()
                                    finally:
                                        conn.close()
                                else:
                                    print(f"❌ Failed to send inventory alert to {email}: {error}")
                            else:
                                print(f"ℹ️ No inventory issues - skipping alert for {email}")
                                
                                # Still update timestamp to avoid checking too frequently
                                conn = sqlite3.connect('searchRack.db')
                                try:
                                    cur = conn.cursor()
                                    cur.execute('''
                                    UPDATE emailer_settings 
                                    SET last_test_sent = ? 
                                    WHERE email = ? AND alert_type = ?
                                ''', (now.isoformat(), email, alert_type))
                                    conn.commit()
                                finally:
                                    conn.close()
                        
                        elif alert_type == 'health':
                            # Collect health stats
                            health_stats = ss_health.collect_health_stats()
                            html_body = ss_health.generate_health_email_html(health_stats)
                            plain_body = ss_health.generate_health_email_plain(health_stats)
                            
                            warnings_count = len(health_stats.get('warnings', []))
                            subject = f"📊 App Health Report{' - ' + str(warnings_count) + ' Warnings' if warnings_count > 0 else ''} - {now.strftime('%Y-%m-%d %H:%M')}"
                            success, error = ss_email_settings.send_email_smtp([email], subject, plain_body, html_body)
                            
                            if success:
                                print(f"✅ Health alert sent to: {email}")
                                
                                # Update last sent time
                                conn = sqlite3.connect('searchRack.db')
                                try:
                                    cur = conn.cursor()
                                    cur.execute('''
                                    UPDATE emailer_settings 
                                    SET last_test_sent = ? 
                                    WHERE email = ? AND alert_type = ?
                                ''', (now.isoformat(), email, alert_type))
                                    conn.commit()
                                finally:
                                    conn.close()
                            else:
                                print(f"❌ Failed to send health alert to {email}: {error}")
                            
                    except Exception as e:
                        print(f"❌ Error sending inventory alert to {email}: {e}")
            
        except Exception as e:
            print(f"❌ Emailer alert worker error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if conn is not None:
                conn.close()


def _start_emailer_alert_thread():
    """Start the emailer alert background thread"""
    emailer_thread = threading.Thread(target=emailer_alert_worker, daemon=True)
    emailer_thread.start()
    print("🚀 Emailer alert thread started")
