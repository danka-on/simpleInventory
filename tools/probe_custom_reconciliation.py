from pathlib import Path
from contextlib import closing
import sqlite3, json
from urllib.request import urlopen
root=Path('/opt/sweetshelves')
for db,table,fields in [('searchRack.db','SEARCHRACK',['ID','TITLE','BARCODE','QUANTITY','ITEM_POSITION','CUSTOM_TITLE']),('bol.db','custom_item_registry',['upc','item_description']),('amazonStore.db','ITEMS',['ASIN','SKU','TITLE','UPC','STATUS','FULFILLMENT_CHANNEL'])]:
    with closing(sqlite3.connect((root/db).as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        columns={r[1].lower():r[1] for r in conn.execute('PRAGMA table_info('+table+')')}
        fields=[f for f in fields if f.lower() in columns]
        title='item_description' if table=='custom_item_registry' else 'TITLE'
        where=' OR '.join('LOWER('+title+') LIKE ?' for _ in range(4))
        rows=conn.execute('SELECT '+','.join(fields)+' FROM '+table+' WHERE '+where,('%yinka%','%ilori%','%dream%','%forever%')).fetchall()
        print(db,table,json.dumps([dict(r) for r in rows]))
with urlopen('http://127.0.0.1:5000/api/listing-reconciliation?refresh=1',timeout=60) as response:
    data=json.load(response)
    print('reconciliation',json.dumps([r for r in data['listings'] if r.get('item_id')=='B0HFTDZV8K' or 'yinka' in r['title'].lower()]))
