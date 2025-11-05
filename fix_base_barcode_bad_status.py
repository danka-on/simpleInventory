"""
Fix base barcodes that incorrectly have 'bad' status in items_prep_status.
Base barcodes (without suffixes) should only have 'good' or 'unchecked' status.
Bad items should always have suffixed barcodes (e.g., 110101-1).

This script will:
1. Find all base barcodes with 'bad' status
2. Check if there are any suffixed versions
3. If suffixed versions exist with 'good' status, change the base to 'good'
4. If no suffixed versions exist, leave as-is (it's actually a bad item)
5. Report all changes
"""
import sqlite3

def fix_base_bad_status():
    conn = sqlite3.connect('bol.db')
    cur = conn.cursor()
    
    # Find all base barcodes (no suffix) with 'bad' status
    cur.execute('''
        SELECT upc, status, reason, updated_at 
        FROM items_prep_status 
        WHERE status = 'bad' AND upc NOT LIKE '%-%'
    ''')
    base_bad_entries = cur.fetchall()
    
    if not base_bad_entries:
        print("✓ No base barcodes with bad status found!")
        conn.close()
        return
    
    print(f"Found {len(base_bad_entries)} base barcode(s) with bad status:")
    
    fixed_count = 0
    skipped_count = 0
    
    for upc, status, reason, updated_at in base_bad_entries:
        print(f"\n  Checking {upc}...")
        
        # Check if there are any suffixed versions of this barcode
        cur.execute('''
            SELECT upc, status FROM items_prep_status 
            WHERE upc LIKE ? AND upc != ?
            ORDER BY upc
        ''', (f'{upc}-%', upc))
        suffixed_entries = cur.fetchall()
        
        if not suffixed_entries:
            print(f"    ⚠ No suffixed entries found - this might be legitimately bad")
            print(f"    Skipping {upc}")
            skipped_count += 1
            continue
        
        # Check if any suffixed entry has 'good' status
        good_suffixed = [e for e in suffixed_entries if e[1] == 'good']
        
        if good_suffixed:
            print(f"    Found {len(good_suffixed)} good suffixed entry(ies):")
            for suf_upc, suf_status in good_suffixed[:3]:  # Show first 3
                print(f"      - {suf_upc}: {suf_status}")
            
            # Change base to 'good' and clear the reason
            print(f"    → Changing {upc} from 'bad' to 'good'")
            cur.execute('''
                UPDATE items_prep_status 
                SET status = 'good', reason = '' 
                WHERE upc = ?
            ''', (upc,))
            fixed_count += 1
        else:
            # All suffixed entries are also bad - probably the base should stay bad
            print(f"    All suffixed entries are also bad - leaving base as bad")
            skipped_count += 1
    
    conn.commit()
    conn.close()
    
    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"  Fixed: {fixed_count}")
    print(f"  Skipped: {skipped_count}")
    print(f"{'='*60}")

if __name__ == '__main__':
    fix_base_bad_status()
