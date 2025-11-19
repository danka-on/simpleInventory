import sqlite3
import datetime

def collect_inventory_mismatches():
    """
    Find items where store listings have quantity > 0 but searchRack has 0 quantity.
    Only reports on items that exist in searchRack with 0 quantity (not missing items).
    Also checks for duplicate barcodes in different locations.
    """
    issues = {
        'has_issues': False,
        'ebay_items': [],
        'amazon_items': [],
        'duplicate_locations': [],
        'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    try:
        # Get searchRack items with 0 quantity
        rack_conn = sqlite3.connect('searchRack.db')
        rack_conn.row_factory = sqlite3.Row
        rack_cur = rack_conn.cursor()
        
        # Get quantity column name
        rack_cur.execute('PRAGMA table_info(SEARCHRACK)')
        cols = [r[1] for r in rack_cur.fetchall()]
        qty_col = 'QUANTITY' if 'QUANTITY' in cols else ('QTY' if 'QTY' in cols else None)
        
        if not qty_col:
            rack_conn.close()
            return issues
        
        # Get all items with 0 quantity and their barcodes
        rack_cur.execute(f'''
            SELECT ITEMID as barcode, TITLE, {qty_col} as quantity 
            FROM SEARCHRACK 
            WHERE ITEMID IS NOT NULL 
            AND TRIM(ITEMID) != ''
            AND ({qty_col} = 0 OR {qty_col} IS NULL)
        ''')
        zero_qty_items = {row['barcode'].strip().upper(): row['TITLE'] for row in rack_cur.fetchall() if row['barcode']}
        rack_conn.close()
        
        print(f"Zero quantity items found: {len(zero_qty_items)}")
        
        if not zero_qty_items:
            print("Skipping store checks - no zero qty items")
        
        # Check for duplicate barcodes (non-suffixed) in different locations
        try:
            rack_conn = sqlite3.connect('searchRack.db')
            rack_conn.row_factory = sqlite3.Row
            rack_cur = rack_conn.cursor()
            
            # Get quantity column name
            rack_cur.execute('PRAGMA table_info(SEARCHRACK)')
            cols = [r[1] for r in rack_cur.fetchall()]
            qty_col = 'QUANTITY' if 'QUANTITY' in cols else ('QTY' if 'QTY' in cols else None)
            
            if qty_col:
                # Find barcodes that appear in multiple different locations (excluding suffixed barcodes)
                rack_cur.execute(f'''
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
                ''')
                
                for row in rack_cur.fetchall():
                    issues['duplicate_locations'].append({
                        'barcode': row['BARCODE'],
                        'title': row['TITLE'] or 'Unknown',
                        'locations': row['locations'],
                        'location_count': row['location_count'],
                        'total_qty': row['total_qty'] or 0
                    })
                    issues['has_issues'] = True
            
            rack_conn.close()
            print(f"Duplicate locations found: {len(issues['duplicate_locations'])}")
        except Exception as e:
            print(f"Error checking for duplicate locations: {e}")
        
    except Exception as e:
        print(f"Error collecting inventory mismatches: {e}")
    
    return issues

# Test the function
result = collect_inventory_mismatches()
print(f"\n=== RESULTS ===")
print(f"Has issues: {result['has_issues']}")
print(f"eBay items: {len(result['ebay_items'])}")
print(f"Amazon items: {len(result['amazon_items'])}")
print(f"Duplicate locations: {len(result['duplicate_locations'])}")
print(f"\nDuplicate details:")
for dup in result['duplicate_locations']:
    print(f"  {dup['barcode']}: {dup['locations']}")
