import sqlite3

conn = sqlite3.connect('searchRack.db')
cur = conn.cursor()

# Get all tables
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cur.fetchall()
print(f"Tables in searchRack.db: {[t[0] for t in tables]}")

# Check SEARCHRACK table (uppercase)
if tables:
    table_name = tables[0][0]
    print(f"\nUsing table: {table_name}")
    
    cur.execute(f"PRAGMA table_info({table_name})")
    columns = cur.fetchall()
    print(f"Columns: {[col[1] for col in columns]}")
    
    # Now search for our ASINs
    asins = ['B00I4EMDUE', 'B089TR3GL1', 'B0CBCV19TR', 'B0BSN3S8TD', 'B002EI2XS8']
    
    for asin in asins:
        cur.execute(f'SELECT BARCODE, TITLE FROM {table_name} WHERE ITEMID = ? LIMIT 1', (asin,))
        result = cur.fetchone()
        if result:
            print(f"\n✅ {asin}: UPC = {result[0]}")
            print(f"   Title: {result[1][:60]}")

conn.close()
