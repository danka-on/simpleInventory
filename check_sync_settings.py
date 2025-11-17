import sqlite3

conn = sqlite3.connect('sync_settings.db')
cur = conn.cursor()

cur.execute('SELECT key, value FROM sync_status')
rows = cur.fetchall()

print("\nAll sync settings:")
print("-" * 50)
for key, value in rows:
    print(f"{key}: {value}")

conn.close()
