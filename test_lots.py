import sqlite3

try:
    conn = sqlite3.connect('rawbol.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Check if table exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='raw_bol_items'")
    table_exists = cur.fetchone()
    print(f"Table exists: {table_exists}")
    
    if table_exists:
        # Get count
        cur.execute('SELECT COUNT(*) FROM raw_bol_items')
        count = cur.fetchone()[0]
        print(f"Total rows: {count}")
        
        # Get distinct lots
        cur.execute('''
            SELECT 
                lot_number,
                MAX(import_date) as import_date
            FROM raw_bol_items
            WHERE lot_number IS NOT NULL 
                AND TRIM(COALESCE(lot_number, '')) != ''
                AND LOWER(lot_number) NOT IN ('nan', 'none', 'null')
            GROUP BY lot_number
            ORDER BY MAX(import_date) DESC
            LIMIT 5
        ''')
        
        lots = cur.fetchall()
        print(f"\nFound {len(lots)} lots:")
        for lot in lots:
            print(f"  - {lot['lot_number']}: {lot['import_date']}")
    
    conn.close()
    print("\nSuccess!")
    
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
