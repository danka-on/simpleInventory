import sqlite3

def update_quantities():
    conn = sqlite3.connect('bol.db')
    cur = conn.cursor()
    
    print("🔍 Analyzing current prep status...")
    
    # Check items_prep_status table
    cur.execute('''
        SELECT status, COUNT(*), SUM(quantity) 
        FROM items_prep_status 
        WHERE status IN ('good', 'bad')
        GROUP BY status
    ''')
    
    print("\nCurrent items_prep_status:")
    print(f"{'Status':<10} | {'Count':<8} | Total Qty")
    print("-" * 40)
    for row in cur.fetchall():
        print(f"{row[0]:<10} | {row[1]:<8} | {row[2]}")
    
    # Get sample of good items
    cur.execute('''
        SELECT upc, quantity FROM items_prep_status 
        WHERE status = 'good' LIMIT 5
    ''')
    good_sample = cur.fetchall()
    
    # Get sample of bad items
    cur.execute('''
        SELECT upc, quantity FROM items_prep_status 
        WHERE status = 'bad' LIMIT 5
    ''')
    bad_sample = cur.fetchall()
    
    print("\n📊 Updating quantities based on prep status...")
    
    # Update good_qty for items with good status
    cur.execute('''
        UPDATE bol_items 
        SET good_qty = (
            SELECT COALESCE(SUM(quantity), 0) 
            FROM items_prep_status 
            WHERE items_prep_status.upc = bol_items.upc 
            AND status = 'good'
        )
        WHERE EXISTS (
            SELECT 1 FROM items_prep_status 
            WHERE items_prep_status.upc = bol_items.upc 
            AND status = 'good'
        )
    ''')
    good_updated = cur.rowcount
    print(f"✅ Updated good_qty for {good_updated} items")
    
    # Update bad_qty from items_prep_status with bad status
    cur.execute('''
        UPDATE bol_items 
        SET bad_qty = (
            SELECT COALESCE(SUM(quantity), 0) 
            FROM items_prep_status 
            WHERE items_prep_status.upc = bol_items.upc 
            AND status = 'bad'
        )
        WHERE EXISTS (
            SELECT 1 FROM items_prep_status 
            WHERE items_prep_status.upc = bol_items.upc 
            AND status = 'bad'
        )
    ''')
    bad_updated = cur.rowcount
    print(f"✅ Updated bad_qty for {bad_updated} items")
    
    # Also add bad_qty from suffixed entries (items marked bad via suffix system)
    cur.execute('''
        UPDATE bol_items 
        SET bad_qty = bad_qty + (
            SELECT COALESCE(SUM(quantity), 0) 
            FROM bol_items AS suffixed
            WHERE suffixed.upc LIKE bol_items.upc || '-%' 
            AND suffixed.temporary = 1
        )
        WHERE upc NOT LIKE '%-%'
        AND EXISTS (
            SELECT 1 FROM bol_items AS suffixed
            WHERE suffixed.upc LIKE bol_items.upc || '-%' 
            AND suffixed.temporary = 1
        )
    ''')
    suffix_updated = cur.rowcount
    print(f"✅ Updated bad_qty from suffixed entries for {suffix_updated} items")
    
    # Recalculate unchecked_qty for all items
    cur.execute('''
        UPDATE bol_items 
        SET unchecked_qty = COALESCE(original_qty, 0) - COALESCE(good_qty, 0) - COALESCE(bad_qty, 0)
        WHERE upc NOT LIKE '%-%'
    ''')
    unchecked_updated = cur.rowcount
    print(f"✅ Recalculated unchecked_qty for {unchecked_updated} base items")
    
    conn.commit()
    
    print("\n" + "="*80)
    print("Sample items with updated quantities:")
    print("="*80)
    
    # Show good items
    print("\nItems with GOOD status:")
    print(f"{'UPC':<15} | {'Original':>8} | {'Good':>5} | {'Bad':>4} | {'Unchecked':>9}")
    print("-" * 65)
    for upc, qty in good_sample:
        cur.execute('''
            SELECT original_qty, good_qty, bad_qty, unchecked_qty 
            FROM bol_items 
            WHERE upc = ? COLLATE NOCASE
        ''', (upc,))
        row = cur.fetchone()
        if row:
            print(f"{upc:<15} | {row[0]:>8} | {row[1]:>5} | {row[2]:>4} | {row[3]:>9}")
    
    # Show bad items
    print("\nItems with BAD status:")
    print(f"{'UPC':<15} | {'Original':>8} | {'Good':>5} | {'Bad':>4} | {'Unchecked':>9}")
    print("-" * 65)
    for upc, qty in bad_sample:
        cur.execute('''
            SELECT original_qty, good_qty, bad_qty, unchecked_qty 
            FROM bol_items 
            WHERE upc = ? COLLATE NOCASE
        ''', (upc,))
        row = cur.fetchone()
        if row:
            print(f"{upc:<15} | {row[0]:>8} | {row[1]:>5} | {row[2]:>4} | {row[3]:>9}")
    
    # Summary statistics
    cur.execute('''
        SELECT 
            COUNT(*) as total_items,
            SUM(COALESCE(original_qty, 0)) as total_original,
            SUM(COALESCE(good_qty, 0)) as total_good,
            SUM(COALESCE(bad_qty, 0)) as total_bad,
            SUM(COALESCE(unchecked_qty, 0)) as total_unchecked
        FROM bol_items
        WHERE upc NOT LIKE '%-%'
    ''')
    stats = cur.fetchone()
    
    print("\n" + "="*80)
    print("Summary Statistics:")
    print(f"  Total items: {stats[0]}")
    print(f"  Total original: {stats[1]}")
    print(f"  Total good: {stats[2]}")
    print(f"  Total bad: {stats[3]}")
    print(f"  Total unchecked: {stats[4]}")
    print(f"  Verification: {stats[1]} = {stats[2]} + {stats[3]} + {stats[4]} ? {stats[1] == stats[2] + stats[3] + stats[4]}")
    print("="*80)
    
    conn.close()
    print("\n✅ Quantity update complete!")

if __name__ == '__main__':
    update_quantities()
