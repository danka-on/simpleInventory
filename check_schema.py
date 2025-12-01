import sqlite3
try:
    conn = sqlite3.connect('bol.db')
    cur = conn.cursor()
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='items_prep_status'")
    res = cur.fetchone()
    if res:
        print(res[0])
    else:
        print("Table not found")
    conn.close()
except Exception as e:
    print(e)
