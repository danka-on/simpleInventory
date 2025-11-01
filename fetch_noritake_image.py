"""
Try to fetch image for the Noritake item from Amazon Catalog API
"""
import sqlite3
from amazon_manager import AmazonManager

# Get the item from amazonStore
conn = sqlite3.connect('amazonStore.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("SELECT ASIN, UPC, IMAGE FROM ITEMS WHERE ASIN = 'B0B57TTYW1'")
row = cur.fetchone()

if row:
    print(f"📦 Item in amazonStore.db:")
    print(f"   ASIN: {row['ASIN']}")
    print(f"   UPC: {row['UPC']}")
    print(f"   Current IMAGE: {row['IMAGE'] or 'NO IMAGE'}")
    
    # Try to fetch from Amazon API
    print(f"\n🔍 Fetching from Amazon Catalog API...")
    try:
        amazon = AmazonManager()
        
        # Try getCatalogItem API
        result = amazon.get_catalog_item(asin=row['ASIN'])
        
        if result and 'images' in result:
            images = result['images']
            if images and len(images) > 0:
                main_image = images[0].get('link') if isinstance(images[0], dict) else None
                print(f"✅ Found image: {main_image}")
                
                # Update amazonStore.db
                cur.execute("UPDATE ITEMS SET IMAGE = ? WHERE ASIN = ?", (main_image, row['ASIN']))
                conn.commit()
                print(f"✅ Updated amazonStore.db with image")
                
                # Update sold.db
                sold_conn = sqlite3.connect('sold.db')
                sold_cur = sold_conn.cursor()
                sold_cur.execute("UPDATE orders SET image = ? WHERE item_id = ?", (main_image, row['ASIN']))
                sold_conn.commit()
                sold_conn.close()
                print(f"✅ Updated sold.db with image")
            else:
                print(f"⚠️ No images in API response")
        else:
            print(f"⚠️ No image data in API response")
            
    except Exception as e:
        print(f"❌ Error fetching from Amazon API: {e}")
        import traceback
        traceback.print_exc()

conn.close()
