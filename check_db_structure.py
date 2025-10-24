import sqlite3

# Check rawbol.db structure
rawbol_conn = sqlite3.connect('rawbol.db')
rawbol_cursor = rawbol_conn.cursor()

print("Tables in rawbol.db:")
rawbol_cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
rawbol_tables = [r[0] for r in rawbol_cursor.fetchall()]
for table in rawbol_tables:
    print(f"  - {table}")
    rawbol_cursor.execute(f"PRAGMA table_info({table})")
    cols = rawbol_cursor.fetchall()
    print(f"    Columns: {[col[1] for col in cols]}")
    rawbol_cursor.execute(f"SELECT COUNT(*) FROM {table}")
    count = rawbol_cursor.fetchone()[0]
    print(f"    Rows: {count}")

rawbol_conn.close()

print("\n" + "="*50)
print("\nTables in bol.db:")
bol_conn = sqlite3.connect('bol.db')
bol_cursor = bol_conn.cursor()

bol_cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
bol_tables = [r[0] for r in bol_cursor.fetchall()]
for table in bol_tables:
    print(f"  - {table}")
    bol_cursor.execute(f"PRAGMA table_info({table})")
    cols = bol_cursor.fetchall()
    print(f"    Columns: {[col[1] for col in cols]}")
    bol_cursor.execute(f"SELECT COUNT(*) FROM {table}")
    count = bol_cursor.fetchone()[0]
    print(f"    Rows: {count}")

bol_conn.close()
