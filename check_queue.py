import sqlite3

conn = sqlite3.connect('bol.db')
cur = conn.cursor()

# Check if print_queue table exists
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='print_queue'")
table_exists = cur.fetchone() is not None
print(f"Print queue table exists: {table_exists}")

if table_exists:
    # Check table structure
    cur.execute("PRAGMA table_info(print_queue)")
    columns = cur.fetchall()
    print("Table columns:")
    for col in columns:
        print(f"  {col[1]} ({col[2]})")

    # Check row count
    cur.execute("SELECT COUNT(*) FROM print_queue")
    count = cur.fetchone()[0]
    print(f"Rows in queue: {count}")

    if count > 0:
        cur.execute("SELECT * FROM print_queue ORDER BY added_at DESC LIMIT 5")
        rows = cur.fetchall()
        print("Recent queue items:")
        for row in rows:
            print(f"  ID: {row[0]}, Title: {row[1]}, Barcode: {row[2]}, Added: {row[3]}")

conn.close()