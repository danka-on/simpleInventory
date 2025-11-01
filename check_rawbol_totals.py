import sqlite3

print("=== Checking rawbol.db for Total Client Cost ===\n")

# Check rawbol.db structure
conn = sqlite3.connect('rawbol.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Get table schema
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cur.fetchall()
print(f"Tables in rawbol.db: {[t['name'] for t in tables]}\n")

# Get schema for raw_bol_items
cur.execute('PRAGMA table_info(raw_bol_items)')
print("raw_bol_items columns:")
for row in cur.fetchall():
    print(f"  {row['name']}: {row['type']}")

# Check if total_client_cost column exists and has data
print("\n=== Sample Data ===")
cur.execute('SELECT * FROM raw_bol_items LIMIT 5')
for row in cur.fetchall():
    client_cost = row['client_cost'] if row['client_cost'] else 0
    total_client_cost = row['total_client_cost'] if row['total_client_cost'] else 0
    print(f"UPC: {row['upc']}")
    print(f"  Description: {row['item_description'][:50] if row['item_description'] else 'N/A'}...")
    print(f"  Quantity: {row['quantity']}")
    print(f"  Client Cost (unit): ${client_cost:.2f}")
    print(f"  Total Client Cost: ${total_client_cost:.2f}")
    print()

# Check for overall totals
print("=== Checking for BOL-level totals ===")
cur.execute('''
    SELECT 
        COUNT(*) as total_items,
        SUM(quantity) as total_quantity,
        SUM(total_client_cost) as sum_total_client_cost,
        SUM(client_cost * quantity) as calculated_total
    FROM raw_bol_items
''')
totals = cur.fetchone()
print(f"Total items in rawbol: {totals['total_items']}")
print(f"Total quantity: {totals['total_quantity']}")
sum_total = totals['sum_total_client_cost'] if totals['sum_total_client_cost'] else 0
calculated_total = totals['calculated_total'] if totals['calculated_total'] else 0
print(f"Sum of total_client_cost column: ${sum_total:.2f}")
print(f"Calculated (client_cost × quantity): ${calculated_total:.2f}")

# Check if there's a separate totals table or metadata
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
all_tables = [t['name'] for t in cur.fetchall()]
print(f"\nAll tables: {all_tables}")

for table in all_tables:
    if 'total' in table.lower() or 'summary' in table.lower() or 'bol' in table.lower():
        print(f"\nFound potential summary table: {table}")
        cur.execute(f'PRAGMA table_info({table})')
        print(f"  Columns: {[r['name'] for r in cur.fetchall()]}")

conn.close()

print("\n=== Comparing with bol.db ===")
bol_conn = sqlite3.connect('bol.db')
bol_conn.row_factory = sqlite3.Row
bol_cur = bol_conn.cursor()

bol_cur.execute('PRAGMA table_info(bol_items)')
print("bol_items columns:")
for row in bol_cur.fetchall():
    print(f"  {row['name']}: {row['type']}")

print("\n=== Sample from bol.db ===")
bol_cur.execute('SELECT upc, client_cost, total_client_cost, quantity FROM bol_items LIMIT 5')
for row in bol_cur.fetchall():
    unit_cost = row['client_cost'] if row['client_cost'] else 0
    total_cost = row['total_client_cost'] if row['total_client_cost'] else 0
    print(f"UPC: {row['upc']} | Unit: ${unit_cost:.2f} | Total: ${total_cost:.2f} | Qty: {row['quantity']}")

bol_conn.close()
