"""
Check if these ItemIDs have images in ebayStore
"""
import sqlite3

ebay = sqlite3.connect('ebayStore.db')
ebay_cur = ebay.cursor()

item_ids = ['187005431687', '187607975019', '187504996424', '187651099708', '187682750565']

print("Checking ebayStore.db for these ItemIDs:")
for item_id in item_ids:
    ebay_cur.execute('SELECT ItemID, Image FROM INVENTORY WHERE ItemID = ?', (item_id,))
    result = ebay_cur.fetchone()
    if result and result[1]:
        img_display = result[1][:80] if len(result[1]) > 80 else result[1]
        print(f"  ✅ {item_id}: {img_display}")
    else:
        print(f"  ❌ {item_id}: No image or not found")

ebay.close()
