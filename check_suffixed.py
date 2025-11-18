import sqlite3

conn = sqlite3.connect('bol.db')
cur = conn.cursor()

# Count items with suffix (non-itemprepped)
cur.execute("""
    SELECT COUNT(*) 
    FROM bol_items 
    WHERE upc LIKE '%-%' 
    AND (itemprepped IS NULL OR itemprepped = 0)
""")
total_suffixed = cur.fetchone()[0]
print(f'Total items with suffix (non-itemprepped): {total_suffixed}')

# Get status breakdown for suffixed items
cur.execute("""
    SELECT 
        CASE WHEN s.status IS NULL THEN 'unchecked' ELSE s.status END as status,
        COUNT(*) as count
    FROM bol_items b 
    LEFT JOIN items_prep_status s ON s.upc = b.upc 
    WHERE b.upc LIKE '%-%' 
    AND (b.itemprepped IS NULL OR b.itemprepped = 0)
    GROUP BY status
""")
print('\nStatus breakdown for suffixed items:')
for row in cur.fetchall():
    print(f'  {row[0]}: {row[1]}')

# Show first 10 unchecked suffixed items
cur.execute("""
    SELECT b.upc, b.item_description, s.status 
    FROM bol_items b 
    LEFT JOIN items_prep_status s ON s.upc = b.upc 
    WHERE b.upc LIKE '%-%' 
    AND (b.itemprepped IS NULL OR b.itemprepped = 0)
    AND s.status IS NULL
    LIMIT 10
""")
print('\nFirst 10 unchecked suffixed items:')
for row in cur.fetchall():
    desc = row[1][:50] if row[1] else 'No description'
    print(f'  {row[0]}: {desc}')

conn.close()
