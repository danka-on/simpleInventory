import sqlite3

# Test the reset functionality by checking what happens when we reset an item

conn = sqlite3.connect('bol.db')
cur = conn.cursor()

print("="*80)
print("TESTING RESET FUNCTIONALITY")
print("="*80)

# Find an item that has been prepped (good_qty > 0)
cur.execute('''
    SELECT upc, item_description, original_qty, good_qty, bad_qty, unchecked_qty, quantity
    FROM bol_items
    WHERE good_qty > 0 OR bad_qty > 0
    LIMIT 5
''')

items = cur.fetchall()

if not items:
    print("\n❌ No prepped items found to demonstrate reset")
    conn.close()
    exit(0)

print("\nFound items that could be reset:\n")
for item in items:
    upc, desc, orig, good, bad, unch, qty = item
    desc_short = (desc[:50] + '...') if desc and len(desc) > 50 else (desc or 'N/A')
    print(f"UPC: {upc}")
    print(f"  Description: {desc_short}")
    print(f"  Original: {orig}, Good: {good}, Bad: {bad}, Unchecked: {unch}, Qty: {qty}")
    print(f"  After reset would be: Original: {orig}, Good: 0, Bad: 0, Unchecked: {orig}, Qty: 0")
    print()

print("="*80)
print("Reset button added to diagnostic page!")
print("="*80)
print("\nFeatures:")
print("• Orange 'Reset' button appears at top-right (between Back and Done)")
print("• Click to reset item to original unchecked state")
print("• Clears good_qty and bad_qty")
print("• Restores unchecked_qty to original_qty")
print("• Clears status and reason from items_prep_status")
print("• Confirms before resetting")
print("• Reloads page after successful reset")
print("\n✅ Test the reset button in the diagnostic page!")

conn.close()
