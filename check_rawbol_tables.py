import sqlite3

conn = sqlite3.connect('rawbol.db')
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cur.fetchall()

print("Tables in rawbol.db:")
for t in tables:
    print(f"  - {t[0]}")

conn.close()
