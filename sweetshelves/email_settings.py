"""Email settings for Sweet Shelves."""

import datetime
import os
import smtplib
import sqlite3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import jsonify, render_template, request
from . import errors as ss_errors, health as ss_health, inventory_alerts as ss_inventory_alerts


def send_email_smtp(to_emails, subject, body, html_body=None):
    """
    Send email using SMTP (Gmail, Outlook, etc.)
    
    Args:
        to_emails: List of recipient email addresses
        subject: Email subject line
        body: Plain text email body
        html_body: Optional HTML email body
    
    Returns:
        (success: bool, error_message: str or None)
    """
    try:
        # Get SMTP settings from environment or database
        smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.getenv('SMTP_PORT', '587'))
        smtp_user = os.getenv('SMTP_USER', '')
        smtp_password = os.getenv('SMTP_PASSWORD', '')
        from_email = os.getenv('SMTP_FROM_EMAIL', smtp_user)
        
        if not smtp_user or not smtp_password:
            return False, 'SMTP credentials not configured. Set SMTP_USER and SMTP_PASSWORD in environment variables or .env file'
        
        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = from_email
        msg['To'] = ', '.join(to_emails)
        
        # Attach text and HTML parts
        text_part = MIMEText(body, 'plain')
        msg.attach(text_part)
        
        if html_body:
            html_part = MIMEText(html_body, 'html')
            msg.attach(html_part)
        
        # Send email
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        
        return True, None
        
    except Exception as e:
        return False, str(e)


def emailer():
    return render_template('emailer.html')


def get_emailer_settings():
    """Get saved emailer settings with per-email configuration"""
    try:
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        
        # Create table if it doesn't exist (don't drop it!)
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
        
        # Get all email settings
        cur.execute('SELECT email, alert_type, interval FROM emailer_settings')
        rows = cur.fetchall()
        
        email_settings = []
        for row in rows:
            email_settings.append({
                'email': row[0],
                'alert_type': row[1] or 'test',
                'interval': row[2] or '1d'
            })
        
        
        return jsonify({
            'success': True,
            'email_settings': email_settings
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def save_emailer_settings():
    """Save emailer settings with per-email configuration"""
    try:
        data = request.json
        email_settings = data.get('email_settings', [])
        
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        
        # Create table if it doesn't exist
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
        
        # Delete all existing settings
        cur.execute('DELETE FROM emailer_settings')
        
        # Insert new settings
        for setting in email_settings:
            email = setting.get('email')
            alert_type = setting.get('alert_type', 'test')
            interval = setting.get('interval', '1d')
            
            cur.execute('''
                INSERT INTO emailer_settings (email, alert_type, interval)
                VALUES (?, ?, ?)
            ''', (email, alert_type, interval))
        
        conn.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def send_test_email():
    """Send a test email"""
    conn = None
    try:
        data = request.json
        emails = data.get('emails', [])
        
        if not emails:
            return jsonify({'success': False, 'error': 'No email addresses provided'}), 400
        
        # Send actual email
        subject = "This is an automated test email from store app"
        body = "Hello, this is the store app speaking."
        
        success, error = send_email_smtp(emails, subject, body)
        
        if not success:
            return jsonify({'success': False, 'error': error}), 500
        
        # Update last test sent time
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        cur.execute('''
            UPDATE emailer_settings 
            SET last_test_sent = ? 
            WHERE id = 1
        ''', (datetime.datetime.now().isoformat(),))
        conn.commit()
        
        print(f"✅ Test email sent successfully to: {', '.join(emails)}")
        
        return jsonify({
            'success': True,
            'message': f'Test email sent to {len(emails)} recipient(s)'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def send_test_inventory_email():
    """Send a test inventory alert email with sample data"""
    try:
        data = request.json
        emails = data.get('emails', [])
        
        if not emails:
            return jsonify({'success': False, 'error': 'No email addresses provided'}), 400
        
        # Generate fake inventory issues for testing
        test_issues = {
            'has_issues': True,
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'ebay_items': [
                {
                    'barcode': '0123456789012',
                    'title': 'Sample Product - Wireless Bluetooth Headphones with Noise Cancellation',
                    'store_qty': 3,
                    'item_id': '123456789012'
                },
                {
                    'barcode': '9876543210987',
                    'title': 'Test Item - Premium Leather Wallet with RFID Protection Technology',
                    'store_qty': 1,
                    'item_id': '987654321098'
                }
            ],
            'amazon_items': [
                {
                    'barcode': '5551234567890',
                    'title': 'Example Product - Stainless Steel Water Bottle 32oz Insulated',
                    'store_qty': 5,
                    'asin': 'B08ABCD1234'
                },
                {
                    'barcode': '4449876543210',
                    'title': 'Demo Item - USB-C Hub Multi-Port Adapter with HDMI and Ethernet',
                    'store_qty': 2,
                    'asin': 'B09WXYZ5678'
                }
            ]
        }
        
        # Generate HTML email
        html_body = ss_inventory_alerts.generate_inventory_email_html(test_issues)
        plain_body = ss_inventory_alerts.generate_inventory_email_plain(test_issues)
        
        # Send email
        subject = f"⚠️ [TEST] Inventory Alert - 4 Items Need Attention - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        success, error = send_email_smtp(emails, subject, plain_body, html_body)
        
        if not success:
            return jsonify({'success': False, 'error': error}), 500
        
        print(f"✅ Test inventory alert sent to: {', '.join(emails)}")
        
        return jsonify({
            'success': True,
            'message': f'Test inventory alert sent to {len(emails)} recipient(s)'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def send_health_email():
    """Send health stats email to configured recipients"""
    try:
        data = request.json
        emails = data.get('emails', [])
        
        if not emails:
            return jsonify({'success': False, 'error': 'No email addresses provided'}), 400
        
        # Collect health stats
        health_data = ss_health.collect_health_stats()
        
        # Generate HTML email
        html_body = ss_health.generate_health_email_html(health_data)
        plain_body = ss_health.generate_health_email_plain(health_data)
        
        # Send email
        subject = f"📊 Store App Health Report - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        success, error = send_email_smtp(emails, subject, plain_body, html_body)
        
        if not success:
            return jsonify({'success': False, 'error': error}), 500
        
        # Clear power event counters after successful email send
        conn = None
        try:
            conn = sqlite3.connect('sync_settings.db')
            cur = conn.cursor()
            cur.execute('DELETE FROM power_events WHERE key IN (?, ?)', 
                       ('undervoltage_count', 'throttle_count'))
            conn.commit()
            print(f"✅ Health email sent to: {', '.join(emails)} - Power event counters reset")
        except Exception:
            print(f"✅ Health email sent to: {', '.join(emails)}")
        finally:
            if conn is not None:
                conn.close()
        
        return jsonify({
            'success': True,
            'message': f'Health report sent to {len(emails)} recipient(s)'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
