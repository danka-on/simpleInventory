import sqlite3

print("=" * 100)
print("COMPREHENSIVE ANALYSIS OF SAME-LOT DUPLICATES IN RAWBOL.DB")
print("=" * 100)

conn = sqlite3.connect('rawbol.db')
cur = conn.cursor()

# Find ALL same-LOT duplicates (not just first 5)
cur.execute('''
    SELECT upc, COUNT(*) as entries, lot_number
    FROM raw_bol_items
    WHERE upc IS NOT NULL AND upc != ''
    GROUP BY upc, lot_number
    HAVING COUNT(*) > 1
    ORDER BY COUNT(*) DESC
''')

same_lot_dups = cur.fetchall()

print(f"\n📊 Found {len(same_lot_dups)} UPCs with same-LOT duplicates\n")
print("=" * 100)

# Analyze each duplicate in detail
total_duplicate_rows = 0

for upc, entries, lot in same_lot_dups:
    print(f"\n📦 UPC {upc} - {entries} entries in LOT {lot}")
    
    cur.execute('''
        SELECT id, item_description, quantity, import_date, created_at, bol_location
        FROM raw_bol_items 
        WHERE upc = ? AND lot_number = ?
        ORDER BY id
    ''', (upc, lot))
    
    rows = cur.fetchall()
    total_duplicate_rows += len(rows) - 1  # -1 because 1 is legitimate
    
    for row_id, desc, qty, imp_date, created_at, bol_loc in rows:
        desc_display = (desc[:40] + '...') if desc and len(desc) > 40 else (desc or 'N/A')
        print(f"  ID {row_id}: Qty={qty} | Import={imp_date} | Created={created_at} | Loc={bol_loc}")
        print(f"    Desc: {desc_display}")
    
    # Check if they were imported at the exact same time (indicates single upload with duplicate rows)
    import_dates = [r[3] for r in rows]
    created_dates = [r[4] for r in rows]
    
    if len(set(import_dates)) == 1 and len(set(created_dates)) == 1:
        print(f"  ⚠️ LIKELY ISSUE: All entries have SAME import_date and created_at!")
        print(f"     → This suggests the BOL Excel file had duplicate rows for this UPC")
    elif len(set(import_dates)) > 1:
        print(f"  ℹ️ NOTE: Different import_dates - this item was added to same LOT multiple times")

print("\n" + "=" * 100)
print(f"\n📊 SUMMARY:")
print(f"  Total UPCs with same-LOT duplicates: {len(same_lot_dups)}")
print(f"  Total extra/duplicate rows: {total_duplicate_rows}")
print(f"  These {total_duplicate_rows} rows should likely be consolidated")

# Check upload_logs to see how many times each LOT was uploaded
print("\n\n🔍 Checking upload_logs for LOTs with duplicates:")
print("=" * 100)

affected_lots = set([lot for _, _, lot in same_lot_dups])
for lot in affected_lots:
    cur.execute('''
        SELECT id, lot_number, filename, upload_date, items_count, status
        FROM upload_logs
        WHERE lot_number = ?
        ORDER BY upload_date
    ''', (str(lot),))
    
    uploads = cur.fetchall()
    print(f"\n📦 LOT {lot} - {len(uploads)} upload(s):")
    for up_id, up_lot, fname, up_date, items_cnt, status in uploads:
        print(f"  Upload ID {up_id}: {fname} | Date: {up_date} | Items: {items_cnt} | Status: {status}")

conn.close()
