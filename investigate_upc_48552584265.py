import sqlite3

print("=" * 100)
print("INVESTIGATING UPC 48552584265 ACROSS ALL DATABASES")
print("=" * 100)

# Check bol.db
print("\n1. Checking bol.db:")
print("-" * 100)
try:
    conn = sqlite3.connect('bol.db')
    cur = conn.cursor()
    cur.execute('''
        SELECT id, upc, lot_number, quantity, original_qty, good_qty, bad_qty, unchecked_qty, temporary, import_date 
        FROM bol_items 
        WHERE upc LIKE ?
    ''', ('48552584265%',))
    rows = cur.fetchall()
    print(f"Found {len(rows)} entries in bol.db:")
    for r in rows:
        print(f"  ID {r[0]}: UPC={r[1]}, LOT={r[2]}, qty={r[3]}, orig={r[4]}, good={r[5]}, bad={r[6]}, unch={r[7]}, temp={r[8]}, import={r[9]}")
    conn.close()
except Exception as e:
    print(f"Error: {e}")

# Check rawbol.db
print("\n2. Checking rawbol.db:")
print("-" * 100)
try:
    conn = sqlite3.connect('rawbol.db')
    cur = conn.cursor()
    # First check what tables exist
    cur.execute('SELECT name FROM sqlite_master WHERE type="table"')
    tables = [t[0] for t in cur.fetchall()]
    print(f"Tables in rawbol.db: {tables}")
    
    # Try to find the UPC in the first table
    if tables:
        table_name = tables[0]
        cur.execute(f'SELECT * FROM {table_name} WHERE upc = ? LIMIT 1', ('48552584265',))
        sample = cur.fetchone()
        if sample:
            # Get column names
            cur.execute(f'PRAGMA table_info({table_name})')
            columns = [col[1] for col in cur.fetchall()]
            print(f"Columns: {columns}")
            
            # Query all entries for this UPC
            cur.execute(f'SELECT * FROM {table_name} WHERE upc = ?', ('48552584265',))
            rows = cur.fetchall()
            print(f"\nFound {len(rows)} entries in {table_name}:")
            for r in rows:
                print(f"  {dict(zip(columns, r))}")
    conn.close()
except Exception as e:
    print(f"Error: {e}")

# Check searchRack.db
print("\n3. Checking searchRack.db:")
print("-" * 100)
try:
    conn = sqlite3.connect('searchRack.db')
    cur = conn.cursor()
    cur.execute('SELECT name FROM sqlite_master WHERE type="table"')
    tables = [t[0] for t in cur.fetchall()]
    print(f"Tables in searchRack.db: {tables}")
    
    for table in tables:
        cur.execute(f'SELECT * FROM {table} WHERE BARCODE = ?', ('48552584265',))
        rows = cur.fetchall()
        if rows:
            print(f"\nFound {len(rows)} entries in {table}:")
            cur.execute(f'PRAGMA table_info({table})')
            columns = [col[1] for col in cur.fetchall()]
            for r in rows:
                print(f"  {dict(zip(columns, r))}")
    conn.close()
except Exception as e:
    print(f"Error: {e}")

print("\n" + "=" * 100)
