import sqlite3

conn = sqlite3.connect('bol.db')
cur = conn.cursor()

# Check for custom items
cur.execute("SELECT upc, item_description, image_url, bol_number FROM bol_items WHERE bol_number = 'CUSTOM' LIMIT 5")
rows = cur.fetchall()

print(f"Found {len(rows)} custom items:")
for row in rows:
    print(f"  UPC: {row[0]}, Title: {row[1][:50]}..., Image: {row[2]}, BOL: {row[3]}")

conn.close()
