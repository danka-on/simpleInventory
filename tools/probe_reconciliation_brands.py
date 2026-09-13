from contextlib import closing
from pathlib import Path
from collections import Counter
import sqlite3,json,re
counts=Counter()
for db,table,col in [('searchRack.db','SEARCHRACK','TITLE'),('amazonStore.db','ITEMS','TITLE'),('ebayStore.db','INVENTORY','Title')]:
    with closing(sqlite3.connect((Path('/opt/sweetshelves')/db).as_uri()+'?mode=ro',uri=True)) as conn:
        for title, in conn.execute('SELECT '+col+' FROM '+table):
            words=re.findall(r"[a-z0-9]+",str(title or '').lower())
            if words: counts[' '.join(words[:2])]+=1
print(json.dumps(counts.most_common(100)))
