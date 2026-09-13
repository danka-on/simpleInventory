"""Read-only reconciliation smoke check; run on the Pi after deployment."""
from collections import Counter
from pathlib import Path
from contextlib import closing
import json
import sqlite3
import time
from urllib.request import urlopen


def main():
    root = Path('/opt/sweetshelves')
    start = time.monotonic()
    with urlopen('http://127.0.0.1:5000/api/listing-reconciliation?refresh=1', timeout=60) as response:
        payload = json.load(response)
    assert payload['success']
    rows = payload['listings']
    expected = {}
    for store, name, sql in (
        ('ebay', 'ebayStore.db', "SELECT COUNT(*) FROM INVENTORY WHERE LOWER(TRIM(COALESCE(List_State,'')))='active'"),
        ('amazon', 'amazonStore.db', "SELECT COUNT(*) FROM ITEMS WHERE LOWER(TRIM(COALESCE(STATUS,'')))='active' AND UPPER(TRIM(COALESCE(FULFILLMENT_CHANNEL,''))) NOT LIKE 'AMAZON%'"),
    ):
        with closing(sqlite3.connect((root / name).as_uri()+'?mode=ro', uri=True)) as conn:
            expected[store] = conn.execute(sql).fetchone()[0]
    assert dict(Counter(row['store'] for row in rows)) == expected
    assert len(rows) == payload['totals']['listings']
    assert sum(v['listings'] for v in payload['totals']['by_state'].values()) == len(rows)
    for row in rows:
        if row['state'] == 'accounted':
            assert row['warehouse'] and row['warehouse_qty'] > 0
            assert not (row['match_kind'] == 'linked' and row.get('link', {}).get('stale'))
        assert len({s['barcode'].lower() for s in row['suggestions']}) == len(row['suggestions'])
    for route, marker in (('/listing-reconciliation', b'linkSelectedButton'), ('/store-manager', b'/listing-reconciliation')):
        with urlopen('http://127.0.0.1:5000'+route, timeout=30) as response:
            assert marker in response.read()
    print(json.dumps({'verified': True, 'active_by_store': expected, 'totals': payload['totals'],
                      'seconds': round(time.monotonic()-start, 2)}, indent=2))


if __name__ == '__main__':
    main()
