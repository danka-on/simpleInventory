import sqlite3

print("=" * 70)
print("CHECKING QUANTITY DISCREPANCIES BETWEEN rawbol.db AND bol.db")
print("=" * 70)

# Connect to both databases
rawbol_conn = sqlite3.connect('rawbol.db')
bol_conn = sqlite3.connect('bol.db')

rawbol_cursor = rawbol_conn.cursor()
bol_cursor = bol_conn.cursor()

# Get all UPCs with their quantities from rawbol.db
print("\n📊 Fetching quantities from rawbol.db...")
rawbol_cursor.execute('''
    SELECT upc, SUM(CAST(quantity AS INTEGER)) as total_qty 
    FROM raw_bol_items 
    WHERE upc IS NOT NULL AND upc != ''
    GROUP BY upc
''')
rawbol_quantities = {row[0]: row[1] for row in rawbol_cursor.fetchall()}
print(f"Found {len(rawbol_quantities)} unique UPCs in rawbol.db")

# Get all UPCs with their quantities from bol.db
print("\n📊 Fetching quantities from bol.db...")
bol_cursor.execute('''
    SELECT upc, CAST(quantity AS INTEGER) as qty
    FROM bol_items 
    WHERE upc IS NOT NULL AND upc != ''
''')
bol_quantities = {row[0]: row[1] for row in bol_cursor.fetchall()}
print(f"Found {len(bol_quantities)} unique UPCs in bol.db")

# Find discrepancies
print("\n🔍 Checking for discrepancies...")
discrepancies = []
for upc, rawbol_qty in rawbol_quantities.items():
    bol_qty = bol_quantities.get(upc, 0)
    if rawbol_qty != bol_qty:
        discrepancies.append({
            'upc': upc,
            'rawbol_qty': rawbol_qty,
            'bol_qty': bol_qty,
            'difference': rawbol_qty - bol_qty
        })

print(f"\n⚠️  Found {len(discrepancies)} UPCs with quantity discrepancies:")
print("\nTop 20 discrepancies:")
for i, disc in enumerate(sorted(discrepancies, key=lambda x: abs(x['difference']), reverse=True)[:20], 1):
    # Get item description for context
    bol_cursor.execute('SELECT item_description FROM bol_items WHERE upc = ? LIMIT 1', (disc['upc'],))
    desc_row = bol_cursor.fetchone()
    desc = desc_row[0][:50] if desc_row and desc_row[0] else 'N/A'
    
    print(f"\n{i}. UPC: {disc['upc']}")
    print(f"   Description: {desc}...")
    print(f"   rawbol.db qty: {disc['rawbol_qty']}")
    print(f"   bol.db qty:    {disc['bol_qty']}")
    print(f"   Difference:    {disc['difference']:+d}")

# Ask for confirmation before fixing
if discrepancies:
    print(f"\n" + "=" * 70)
    print(f"READY TO FIX {len(discrepancies)} QUANTITY DISCREPANCIES")
    print("=" * 70)
    print("\nThis will update bol.db quantities to match rawbol.db")
    
    response = input("\nProceed with fixing? (yes/no): ").strip().lower()
    
    if response == 'yes':
        print("\n🔧 Fixing quantities in bol.db...")
        fixed_count = 0
        failed_count = 0
        
        for disc in discrepancies:
            try:
                bol_cursor.execute('''
                    UPDATE bol_items 
                    SET quantity = ? 
                    WHERE upc = ?
                ''', (disc['rawbol_qty'], disc['upc']))
                fixed_count += 1
                print(f"✓ Fixed UPC {disc['upc']}: {disc['bol_qty']} → {disc['rawbol_qty']}")
            except Exception as e:
                failed_count += 1
                print(f"✗ Failed to fix UPC {disc['upc']}: {e}")
        
        # Commit changes
        bol_conn.commit()
        
        print(f"\n" + "=" * 70)
        print(f"✅ FIX COMPLETE")
        print("=" * 70)
        print(f"Fixed: {fixed_count} UPCs")
        print(f"Failed: {failed_count} UPCs")
        
        # Verify the fix
        print("\n🔍 Verifying fixes...")
        verification_errors = 0
        for disc in discrepancies[:10]:
            bol_cursor.execute('SELECT quantity FROM bol_items WHERE upc = ?', (disc['upc'],))
            result = bol_cursor.fetchone()
            if result:
                new_qty = int(result[0])
                if new_qty == disc['rawbol_qty']:
                    print(f"✓ UPC {disc['upc']}: Verified correct ({new_qty})")
                else:
                    print(f"✗ UPC {disc['upc']}: Still incorrect ({new_qty} != {disc['rawbol_qty']})")
                    verification_errors += 1
        
        if verification_errors == 0:
            print("\n✅ All verified items are correct!")
        else:
            print(f"\n⚠️  {verification_errors} verification errors found")
    else:
        print("\n❌ Fix cancelled by user")
else:
    print("\n✅ No discrepancies found! All quantities match.")

# Close connections
rawbol_conn.close()
bol_conn.close()

print("\n" + "=" * 70)
print("CHECK COMPLETE")
print("=" * 70)
