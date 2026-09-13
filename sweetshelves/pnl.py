"""Profit & loss page: real payouts versus lot spend, plus per-lot attribution."""

import datetime
import sqlite3
from flask import jsonify, render_template, request
from . import config as ss_config, errors as ss_errors


EXPENSE_CATEGORIES = (
    'Shipping supplies', 'Labels & printing', 'Marketplace subscription', 'Storage & rent',
    'Software', 'Transport & fuel', 'Labor', 'Other',
)


def _sold_db_path():
    return str(ss_config.BASE_DIR / 'sold.db')


def _ensure_expenses_table(conn):
    conn.execute('''
        CREATE TABLE IF NOT EXISTS pnl_expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            expense_date TEXT NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')


def pnl_page():
    return render_template('pnl.html', expense_categories=EXPENSE_CATEGORIES)


def api_pnl():
    """Everything the P&L page shows, computed read-only from the local databases."""
    from pnl_report import build_pnl
    try:
        return jsonify(build_pnl(ss_config.BASE_DIR))
    except Exception as exc:
        ss_config.logger.exception('P&L report failed')
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'pnl')}), 500


def api_pnl_expenses():
    """List (GET) or record (POST) an expense that no marketplace payout covers."""
    conn = None
    try:
        conn = sqlite3.connect(_sold_db_path(), timeout=30.0)
        conn.row_factory = sqlite3.Row
        _ensure_expenses_table(conn)
        if request.method == 'POST':
            data = request.get_json(silent=True) or request.form.to_dict() or {}
            expense_date = str(data.get('expense_date') or '').strip()[:10]
            category = str(data.get('category') or 'Other').strip() or 'Other'
            note = str(data.get('note') or '').strip()
            try:
                amount = round(float(data.get('amount')), 2)
            except (TypeError, ValueError):
                return jsonify({'success': False, 'error': 'Amount must be a number.'}), 400
            if amount <= 0:
                return jsonify({'success': False, 'error': 'Amount must be above zero.'}), 400
            try:
                datetime.datetime.strptime(expense_date, '%Y-%m-%d')
            except ValueError:
                return jsonify({'success': False, 'error': 'Date must be YYYY-MM-DD.'}), 400
            cur = conn.execute(
                'INSERT INTO pnl_expenses (expense_date, category, amount, note) VALUES (?, ?, ?, ?)',
                (expense_date, category, amount, note),
            )
            conn.commit()
            return jsonify({'success': True, 'id': cur.lastrowid})
        rows = conn.execute(
            'SELECT id, expense_date, category, amount, note FROM pnl_expenses ORDER BY expense_date DESC, id DESC'
        ).fetchall()
        return jsonify({'success': True, 'expenses': [dict(row) for row in rows],
                        'categories': list(EXPENSE_CATEGORIES)})
    except Exception as exc:
        ss_config.logger.exception('P&L expenses failed')
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'pnl_expenses')}), 500
    finally:
        if conn is not None:
            conn.close()


def api_pnl_expense_delete(expense_id):
    conn = None
    try:
        conn = sqlite3.connect(_sold_db_path(), timeout=30.0)
        _ensure_expenses_table(conn)
        cur = conn.execute('DELETE FROM pnl_expenses WHERE id = ?', (int(expense_id),))
        conn.commit()
        if cur.rowcount == 0:
            return jsonify({'success': False, 'error': 'Expense not found.'}), 404
        return jsonify({'success': True})
    except Exception as exc:
        ss_config.logger.exception('P&L expense delete failed')
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'pnl_expense_delete')}), 500
    finally:
        if conn is not None:
            conn.close()
