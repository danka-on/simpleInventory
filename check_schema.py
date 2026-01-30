from DBmanager import connect_db
try:
    with connect_db('bol.db') as conn:
        cur = conn.cursor()
        cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='items_prep_status'")
        res = cur.fetchone()
        if res:
            print(res[0])
        else:
            print("Table not found")
except Exception as e:
    print(e)
