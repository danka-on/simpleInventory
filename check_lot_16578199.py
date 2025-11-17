"""
Check for LOT# 16578199 in bol.db and rawbol.db
"""
import sqlite3

print("=" * 80)
print("Checking for LOT# 16578199")
print("=" * 80)

# Check bol.db
print("\n1. Checking bol.db (bol_items table):")
print("-" * 80)
try:
    conn = sqlite3.connect('bol.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Check exact match
    cur.execute("SELECT * FROM bol_items WHERE lot_number = '16578199'")
    results = cur.fetchall()
    
    if results:
        print(f"✅ Found {len(results)} exact match(es):")
        for i, row in enumerate(results[:10], 1):  # Show first 10
            try:
                upc = row['UPC'] if 'UPC' in row.keys() else 'N/A'
                title = row['TITLE'][:50] if 'TITLE' in row.keys() else 'N/A'
                qty = row['QTY'] if 'QTY' in row.keys() else 'N/A'
                location = row['LOCATION'] if 'LOCATION' in row.keys() else 'N/A'
                print(f"   {i}. UPC: {upc}, Title: {title}, Qty: {qty}, Location: {location}")
            except Exception as e:
                print(f"   {i}. Error reading row: {e}")
        if len(results) > 10:
            print(f"   ... and {len(results) - 10} more")
    else:
        print("❌ No exact match found")
    
    # Check with suffixes
    cur.execute("SELECT * FROM bol_items WHERE lot_number LIKE '16578199%'")
    suffix_results = cur.fetchall()
    
    if suffix_results and len(suffix_results) > len(results):
        print(f"\n✅ Found {len(suffix_results)} result(s) with suffixes:")
        for row in suffix_results:
            print(f"   Lot: {row['lot_number']}, UPC: {row['UPC']}, Title: {row['TITLE'][:50]}")
    
    conn.close()
    
except Exception as e:
    print(f"❌ Error checking bol.db: {e}")

# Check rawbol.db
print("\n2. Checking rawbol.db:")
print("-" * 80)
try:
    conn = sqlite3.connect('rawbol.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Get table name (should be raw_bol_data or similar)
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row['name'] for row in cur.fetchall()]
    
    found_in_rawbol = False
    for table in tables:
        try:
            # First check what columns exist
            cur.execute(f"PRAGMA table_info({table})")
            columns = [col[1] for col in cur.fetchall()]
            
            # Build query based on available columns
            lot_col = None
            if 'LOT' in columns:
                lot_col = 'LOT'
            elif 'LOT#' in columns:
                lot_col = '`LOT#`'
            elif 'lot_number' in columns:
                lot_col = 'lot_number'
            
            if lot_col:
                cur.execute(f"SELECT * FROM {table} WHERE {lot_col} LIKE '%16578199%'")
                results = cur.fetchall()
                
                if results:
                    print(f"✅ Found {len(results)} result(s) in table '{table}':")
                    for i, row in enumerate(results[:5], 1):  # Show first 5
                        print(f"   {i}. {dict(row)}")
                    if len(results) > 5:
                        print(f"   ... and {len(results) - 5} more")
                    found_in_rawbol = True
        except Exception as e:
            print(f"⚠️ Error checking table '{table}': {e}")
    
    if not found_in_rawbol:
        print("❌ No results found in any rawbol.db tables")
    
    conn.close()
    
except Exception as e:
    print(f"❌ Error checking rawbol.db: {e}")

print("\n" + "=" * 80)
print("Search complete")
print("=" * 80)
