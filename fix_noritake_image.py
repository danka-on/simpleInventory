"""
Properly extract and update the Noritake image from Amazon API response
"""
import sqlite3
from amazon_manager import AmazonManager

print("🔍 Fetching Noritake item from Amazon Catalog API...\n")

try:
    amazon = AmazonManager()
    result = amazon.get_catalog_item(asin='B0B57TTYW1')
    
    if result and 'images' in result:
        print("✅ Found images in API response")
        
        # Extract MAIN image with largest size
        image_url = None
        for image_group in result['images']:
            if 'images' in image_group:
                for img in image_group['images']:
                    if img.get('variant') == 'MAIN':
                        height = img.get('height', 0)
                        link = img.get('link', '')
                        print(f"   MAIN image: {link} ({height}px)")
                        
                        # Get the largest MAIN image
                        if height >= 500 and not image_url:
                            image_url = link
                        elif height > 1000:  # Prefer high-res
                            image_url = link
        
        if image_url:
            print(f"\n✅ Selected image: {image_url}")
            
            # Update amazonStore.db
            amazon_conn = sqlite3.connect('amazonStore.db')
            amazon_cur = amazon_conn.cursor()
            amazon_cur.execute("UPDATE ITEMS SET IMAGE = ? WHERE ASIN = ?", (image_url, 'B0B57TTYW1'))
            amazon_conn.commit()
            amazon_conn.close()
            print(f"✅ Updated amazonStore.db")
            
            # Update sold.db
            sold_conn = sqlite3.connect('sold.db')
            sold_cur = sold_conn.cursor()
            sold_cur.execute("UPDATE orders SET image = ? WHERE item_id = ?", (image_url, 'B0B57TTYW1'))
            affected = sold_cur.rowcount
            sold_conn.commit()
            sold_conn.close()
            print(f"✅ Updated {affected} sold orders with image")
        else:
            print("❌ No suitable MAIN image found")
    else:
        print("❌ No images in API response")
        
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
