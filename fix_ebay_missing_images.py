"""
Fetch and update images for all eBay listings with "No image"
"""
import requests
import xml.etree.ElementTree as ET
import sqlite3
import time
import os

# eBay credentials
EBAY_TOKEN = 'v^1.1#i^1#I^3#f^0#p^3#r^1#t^Ul4xMF82OkYwRjY2Q0VFOUY1QUM0MkEyMjkyMDY5Q0E5NjY0NjIxXzFfMSNFXjI2MA=='
EBAY_APP_ID = os.getenv("EBAY_PROD_APP_ID")
EBAY_DEV_ID = os.getenv("EBAY_PROD_DEV_ID")
EBAY_CERT_ID = os.getenv("EBAY_PROD_CERT_ID")

print("Fetching images for eBay listings with 'No image'...")
print("=" * 80)

# Get all items with "No image"
conn = sqlite3.connect('ebayStore.db')
cur = conn.cursor()

cur.execute("SELECT ItemID, Title FROM INVENTORY WHERE Image = 'No image'")
items = cur.fetchall()

print(f"Found {len(items)} listings without images\n")

updated = 0
not_found = 0
errors = 0

url = "https://api.ebay.com/ws/api.dll"

for item_id, title in items:
    try:
        headers = {
            "X-EBAY-API-SITEID": "0",
            "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
            "X-EBAY-API-CALL-NAME": "GetItem",
            "X-EBAY-API-APP-NAME": EBAY_APP_ID,
            "X-EBAY-API-DEV-NAME": EBAY_DEV_ID,
            "X-EBAY-API-CERT-NAME": EBAY_CERT_ID,
            "Content-Type": "text/xml"
        }

        xml_request = f'''<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials>
    <eBayAuthToken>{EBAY_TOKEN}</eBayAuthToken>
  </RequesterCredentials>
  <ItemID>{item_id}</ItemID>
  <DetailLevel>ReturnAll</DetailLevel>
</GetItemRequest>'''

        response = requests.post(url, data=xml_request, headers=headers)
        
        if response.status_code == 200:
            root = ET.fromstring(response.content)
            ns = {'ebay': 'urn:ebay:apis:eBLBaseComponents'}
            
            # Check for errors
            ack = root.find('ebay:Ack', ns)
            if ack is not None and ack.text == 'Success':
                # Get picture details
                picture_details = root.find('.//ebay:PictureDetails', ns)
                if picture_details is not None:
                    # Get the first picture URL
                    pic_url = picture_details.find('ebay:PictureURL', ns)
                    if pic_url is not None and pic_url.text:
                        image_url = pic_url.text
                        
                        # Update ebayStore.db
                        cur.execute("UPDATE INVENTORY SET Image = ? WHERE ItemID = ?", (image_url, item_id))
                        conn.commit()
                        
                        # Also update sold.db if this item is a sold order
                        sold_conn = sqlite3.connect('sold.db')
                        sold_cur = sold_conn.cursor()
                        sold_cur.execute("UPDATE orders SET image = ? WHERE item_id = ? AND (image IS NULL OR image = '' OR LOWER(image) = 'no image')", 
                                       (image_url, item_id))
                        sold_conn.commit()
                        sold_conn.close()
                        
                        updated += 1
                        title_display = (title[:60] + "...") if title and len(title) > 60 else (title or "No title")
                        print(f"OK {item_id} | {title_display}")
                    else:
                        not_found += 1
                        if not_found <= 10:
                            print(f"No image URL {item_id} | {title[:60] if title else 'No title'}")
                else:
                    not_found += 1
                    if not_found <= 10:
                        print(f"No PictureDetails {item_id} | {title[:60] if title else 'No title'}")
            else:
                # Check for specific errors
                error_elem = root.find('.//ebay:Errors/ebay:LongMessage', ns)
                error_msg = error_elem.text if error_elem is not None else "Unknown error"
                errors += 1
                if errors <= 10:
                    print(f"Error {item_id}: {error_msg[:80]}")
        else:
            errors += 1
            if errors <= 10:
                print(f"HTTP Error {item_id}: {response.status_code}")
        
        # Rate limiting - be respectful to eBay API
        # GetItem has 5000 calls per day limit
        time.sleep(0.5)  # Half second between requests
        
    except Exception as e:
        errors += 1
        if errors <= 10:
            print(f"Exception {item_id}: {str(e)[:80]}")

conn.close()

print("\n" + "=" * 80)
print(f"Results:")
print(f"  Updated: {updated}")
print(f"  Not found: {not_found}")
print(f"  Errors: {errors}")
print(f"  Total processed: {len(items)}")

# Final stats
conn = sqlite3.connect('ebayStore.db')
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM INVENTORY WHERE Image IS NOT NULL AND Image != '' AND Image != 'No image'")
with_images = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM INVENTORY WHERE Image = 'No image' OR Image IS NULL OR Image = ''")
without_images = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM INVENTORY")
total = cur.fetchone()[0]
conn.close()

print(f"\neBay Store Inventory:")
print(f"  Total items: {total}")
print(f"  Items with images: {with_images} ({int(with_images/total*100)}%)")
print(f"  Items without images: {without_images}")

print("\nComplete!")
