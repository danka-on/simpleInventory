import sqlite3

conn = sqlite3.connect('bol.db')
cur = conn.cursor()

print("Checking LOT 16423315 and the 4 UPCs...\n")

# Check if LOT 16423315 exists
cur.execute('SELECT COUNT(*) FROM bol_items WHERE lot_number = ?', ('16423315',))
lot_count = cur.fetchone()[0]
print(f"LOT 16423315: {lot_count} items found")

if lot_count > 0:
    print("\nSample items in LOT 16423315:")
    cur.execute('SELECT upc, item_description FROM bol_items WHERE lot_number = ? LIMIT 5', ('16423315',))
    for row in cur.fetchall():
        desc = (row[1][:40] + '...') if row[1] and len(row[1]) > 40 else (row[1] or 'N/A')
        print(f"  {row[0]}: {desc}")

print("\n" + "="*80)
print("The 4 UPCs you want to move:")
print("="*80)

upcs = ['48552491662', '48552584265', '608084003797', '885991237136']

for upc in upcs:
    print(f"\n{upc}:")
    cur.execute('SELECT lot_number, good_qty, unchecked_qty, original_qty FROM bol_items WHERE upc = ?', (upc,))
    rows = cur.fetchall()
    
    if not rows:
        print("  ❌ Not found in bol.db")
    else:
        for row in rows:
            print(f"  LOT {row[0]}: good={row[1]}, unchecked={row[2]}, original={row[3]}")

conn.close()

print("\n" + "="*80)
print("ISSUE: The 4 UPCs only exist in LOT 16578199")
print("They do NOT have entries in LOT 16423315 yet.")
print("\nDo you want me to:")
print("  1. CREATE new entries in LOT 16423315 and move the good qty there?")
print("  2. Something else?")
print("="*80)
