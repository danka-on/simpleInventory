import sqlite3

conn = sqlite3.connect('searchRack.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Get quantity column name
cur.execute('PRAGMA table_info(SEARCHRACK)')
cols = [r[1] for r in cur.fetchall()]
qty_col = 'QUANTITY' if 'QUANTITY' in cols else ('QTY' if 'QTY' in cols else None)

print(f"Quantity column: {qty_col}\n")

# Find barcodes that appear in multiple different locations (excluding suffixed barcodes)
query = f'''
    SELECT 
        BARCODE,
        TITLE,
        GROUP_CONCAT(ITEM_POSITION || ' (Qty: ' || {qty_col} || ')', ', ') as locations,
        COUNT(DISTINCT ITEM_POSITION) as location_count,
        SUM({qty_col}) as total_qty
    FROM SEARCHRACK
    WHERE BARCODE IS NOT NULL 
    AND TRIM(BARCODE) != ''
    AND BARCODE NOT LIKE '%-%'
    AND ITEM_POSITION IS NOT NULL
    AND TRIM(ITEM_POSITION) != ''
    AND ({qty_col} > 0 OR {qty_col} IS NULL)
    GROUP BY BARCODE
    HAVING COUNT(DISTINCT ITEM_POSITION) > 1
    ORDER BY location_count DESC, BARCODE
'''

print("Running duplicate detection query...\n")
cur.execute(query)
rows = cur.fetchall()

print(f"Found {len(rows)} barcodes in multiple locations:\n")
for row in rows:
    print(f"Barcode: {row['BARCODE']}")
    print(f"  Title: {row['TITLE']}")
    print(f"  Locations: {row['locations']}")
    print(f"  Location Count: {row['location_count']}")
    print(f"  Total Qty: {row['total_qty']}")
    print()

conn.close()
