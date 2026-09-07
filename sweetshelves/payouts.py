"""Payouts for Sweet Shelves."""

import sqlite3
from flask import jsonify, render_template, request
from . import errors as ss_errors, integrations as ss_integrations


def payouts_page():
    """Payouts/settlements management page."""
    return render_template('payouts.html')


def api_get_payouts():
    """Get all payouts with optional filters."""
    conn = None
    try:
        store = request.args.get('store', '').strip()
        status = request.args.get('status', '').strip()
        start_date = request.args.get('start_date', '').strip()
        end_date = request.args.get('end_date', '').strip()
        
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Build query with optional filters (default: include non-zero payouts)
        query = 'SELECT * FROM payouts WHERE COALESCE(amount, 0) != 0'
        params = []
        
        if store:
            query += ' AND store = ?'
            params.append(store)
        
        if status:
            query += ' AND LOWER(status) = LOWER(?)'
            params.append(status)
        
        if start_date:
            query += ' AND start_date >= ?'
            params.append(start_date)
        
        if end_date:
            query += ' AND end_date <= ?'
            params.append(end_date)
        
        query += ' ORDER BY start_date DESC'
        
        cur.execute(query, params)
        rows = cur.fetchall()
        
        payouts = []
        total_amount = 0
        
        for row in rows:
            payout = {
                'id': row['id'],
                'store': row['store'],
                'settlement_id': row['settlement_id'],
                'start_date': row['start_date'],
                'end_date': row['end_date'],
                'payout_date': row['payout_date'],
                'amount': row['amount'],
                'currency': row['currency'],
                'status': row['status'],
                'transaction_count': row['transaction_count'],
                'synced_at': row['synced_at']
            }
            payouts.append(payout)
            
            # Sum amounts (convert to USD if needed)
            if row['currency'] == 'USD':
                total_amount += row['amount']
        
        # Calculate stats
        stats = {
            'total_payouts': len(payouts),
            'total_amount': total_amount,
            'open_count': len([p for p in payouts if p['status'] == 'Open']),
            'closed_count': len([p for p in payouts if p['status'] == 'Closed'])
        }
        
        
        return jsonify({
            'success': True,
            'payouts': payouts,
            'stats': stats
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_sync_payouts():
    """Manually trigger payout sync for Amazon and/or eBay."""
    try:
        # Accept both JSON and form POSTs
        if request.is_json:
            data = request.get_json() or {}
        else:
            data = request.form.to_dict() if request.form else {}
        store = data.get('store', 'all').lower()  # 'amazon', 'ebay', or 'all'

        results = {'amazon': 0, 'ebay': 0}
        errors = []

        # Sync Amazon payouts
        if store in ['amazon', 'all']:
            if ss_integrations.AMAZON_AVAILABLE:
                try:
                    amazon = ss_integrations.AmazonManager()
                    results['amazon'] = amazon.sync_settlements_to_db(days_back=90)
                except Exception as e:
                    errors.append(f"Amazon: {str(e)}")
            else:
                errors.append("Amazon integration not available")

        # Sync eBay payouts
        if store in ['ebay', 'all']:
            try:
                from ebay_manager import EbayManager
                ebay = EbayManager()
                results['ebay'] = ebay.sync_payouts_to_db(days_back=90)
            except Exception as e:
                errors.append(f"eBay: {str(e)}")

        total_synced = results['amazon'] + results['ebay']

        message = f"Synced {results['amazon']} Amazon + {results['ebay']} eBay payouts"
        if errors:
            message += f" (Errors: {'; '.join(errors)})"

        return jsonify({
            'success': True,
            'message': message,
            'synced_count': total_synced,
            'details': results,
            'errors': errors
        })

    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
