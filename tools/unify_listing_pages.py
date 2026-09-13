"""Apply narrow function replacements, preserving other working-tree edits."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_function(path, name, source):
    text = path.read_text(encoding='utf-8')
    node = next(n for n in ast.parse(text).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
    lines = text.splitlines(keepends=True)
    lines[node.lineno-1:node.end_lineno] = [source.rstrip()+'\n']
    updated = ''.join(lines)
    ast.parse(updated)
    path.write_text(updated,encoding='utf-8')


if __name__ == '__main__':
    replace_function(ROOT/'sweetshelves/listing_alerts.py','api_listing_helper_scan','''
def api_listing_helper_scan():
    """Serve global alerts from the same Listings & Stock calculation."""
    from . import listing_reconciliation
    try:
        return jsonify(listing_reconciliation.listing_alert_summary(ss_sync._sync_manager_overdue_alert()))
    except Exception as e:
        return jsonify({'success':False,'error':ss_errors._safe_error(e,'listing-stock-alerts')}),500
''')
