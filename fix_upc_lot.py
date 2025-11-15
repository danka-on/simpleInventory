import sqlite3

upc = "858557007115"
selected_lot = "16423315"  # Latest lot

# Update the bol.db entry to use the selected lot
conn = sqlite3.connect('bol.db')
cur = conn.cursor()

# Get the lot's import_date from rawbol
conn2 = sqlite3.connect('rawbol.db')
cur2 = conn2.cursor()
cur2.execute('SELECT import_date FROM raw_bol_items WHERE lot_number = ? LIMIT 1', (selected_lot,))
row = cur2.fetchone()
import_date = row[0] if row else None
conn2.close()

print(f"Updating UPC {upc} to LOT {selected_lot} with import_date {import_date}")

# Update the base entry
cur.execute('''
    UPDATE bol_items 
    SET lot_number = ?, import_date = ?
    WHERE upc = ? AND (temporary IS NULL OR temporary = 0)
''', (selected_lot, import_date, upc))

affected = cur.rowcount
conn.commit()
conn.close()

print(f"Updated {affected} row(s)")
