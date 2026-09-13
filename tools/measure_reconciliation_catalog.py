"""Read-only catalog sizing; emits counts, never product records or credentials."""
import json
import sqlite3
from pathlib import Path

root = Path('/opt/sweetshelves')
with sqlite3.connect((root/'searchRack.db').as_uri()+'?mode=ro', uri=True) as conn:
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT ID, BARCODE, TITLE, QUANTITY, WAREHOUSE_NOTE FROM SEARCHRACK').fetchall()
    stocked = [dict(r) for r in rows if str(r['BARCODE'] or '').strip() and float(r['QUANTITY'] or 0)>0]
    text = json.dumps(stocked,ensure_ascii=False,separators=(',',':'))
    print(json.dumps({'stocked_rows':len(stocked),'distinct_barcodes':len({str(r['BARCODE']) for r in stocked}),
                      'catalog_characters':len(text),'rough_tokens_at_3_chars':round(len(text)/3)}))
