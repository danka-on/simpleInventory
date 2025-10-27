"""
Check if eBay listing actually has image URL
"""
import sqlite3

conn = sqlite3.connect('ebayStore.db')
cur = conn.cursor()

cur.execute("""
    SELECT Title, ItemID, Image, UPC, URL, List_State 
    FROM INVENTORY 
    WHERE ItemID = '187682750565'
""")

row = cur.fetchone()

if row:
    print("eBay Listing Details:")
    print(f"  Title: {row[0]}")
    print(f"  ItemID: {row[1]}")
    print(f"  Image: {row[2]}")
    print(f"  UPC: {row[3]}")
    print(f"  URL: {row[4]}")
    print(f"  List_State: {row[5]}")
    
    # Check if this is actually "No image" string or an actual URL
    if row[2] and row[2] != 'No image':
        print(f"\n✅ Has image URL: {row[2]}")
    else:
        print(f"\n❌ Image is: '{row[2]}'")
        print("\nThis means the eBay listing sync stored 'No image' instead of the actual image URL.")
        print("The eBay API might not have returned the image, or it was stored incorrectly.")

conn.close()
