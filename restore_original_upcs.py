"""
Restore original UPCs that were overwritten with ASINs
"""
import sqlite3

# Recovered UPCs from order history
recovered_upcs = {
    'B089TR3GL1': '194372011860',
}

# Previously manually found UPCs
manual_upcs = {
    'B0DX3VNNVQ': '37725617275',  # Already fixed
    'B07B5SVLN8': '790824200228'   # Already fixed
}

print("🔄 Restoring original UPCs that were overwritten...\n")

sold_conn = sqlite3.connect('sold.db')
sold_cur = sold_conn.cursor()

amazon_conn = sqlite3.connect('amazonStore.db')
amazon_cur = amazon_conn.cursor()

fixed_count = 0

for asin, upc in recovered_upcs.items():
    print(f"Fixing {asin} → {upc}")
    
    # Update amazonStore.db
    amazon_cur.execute('UPDATE ITEMS SET UPC = ? WHERE ASIN = ?', (upc, asin))
    print(f"  ✅ Updated amazonStore.db")
    
    # Update ALL orders in sold.db with this ASIN
    sold_cur.execute('UPDATE orders SET barcode = ? WHERE item_id = ?', (upc, asin))
    updated = sold_cur.rowcount
    print(f"  ✅ Updated {updated} orders in sold.db\n")
    
    fixed_count += 1

sold_conn.commit()
amazon_conn.commit()

sold_conn.close()
amazon_conn.close()

print(f"✅ Restored {fixed_count} UPCs")

# Show remaining ASINs
print("\n📋 Remaining items with ASINs (no UPC found):")
remaining = ['B00I4EMDUE', 'B0CBCV19TR', 'B0BSN3S8TD', 'B002EI2XS8']
for asin in remaining:
    sold_conn = sqlite3.connect('sold.db')
    sold_cur = sold_conn.cursor()
    sold_cur.execute('SELECT title FROM orders WHERE item_id = ? LIMIT 1', (asin,))
    result = sold_cur.fetchone()
    title = result[0] if result else "Unknown"
    print(f"  {asin}: {title[:60]}")
    sold_conn.close()
