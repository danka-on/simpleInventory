"""
Fetch current image for ItemID 187682750565 from eBay API
"""
import requests
import xml.etree.ElementTree as ET
import os

ITEM_ID = "187682750565"

# eBay auth token (from app.py)
EBAY_TOKEN = 'v^1.1#i^1#I^3#f^0#p^3#r^1#t^Ul4xMF82OkYwRjY2Q0VFOUY1QUM0MkEyMjkyMDY5Q0E5NjY0NjIxXzFfMSNFXjI2MA=='

# Use GetItem API call
url = "https://api.ebay.com/ws/api.dll"

headers = {
    "X-EBAY-API-SITEID": "0",
    "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
    "X-EBAY-API-CALL-NAME": "GetItem",
    "X-EBAY-API-APP-NAME": os.getenv("EBAY_PROD_APP_ID"),
    "X-EBAY-API-DEV-NAME": os.getenv("EBAY_PROD_DEV_ID"),
    "X-EBAY-API-CERT-NAME": os.getenv("EBAY_PROD_CERT_ID"),
    "Content-Type": "text/xml"
}

xml_request = f'''<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials>
    <eBayAuthToken>{EBAY_TOKEN}</eBayAuthToken>
  </RequesterCredentials>
  <ItemID>{ITEM_ID}</ItemID>
  <DetailLevel>ReturnAll</DetailLevel>
</GetItemRequest>'''

print(f"Fetching eBay item {ITEM_ID}...")

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
            # Get all picture URLs
            pic_urls = picture_details.findall('ebay:PictureURL', ns)
            if pic_urls:
                print(f"\n✅ Found {len(pic_urls)} image(s):")
                for i, pic in enumerate(pic_urls, 1):
                    print(f"  {i}. {pic.text}")
                
                # Get the first one
                main_image = pic_urls[0].text
                print(f"\nMain image URL: {main_image}")
                
                # Update ebayStore.db
                import sqlite3
                conn = sqlite3.connect('ebayStore.db')
                cur = conn.cursor()
                cur.execute("UPDATE INVENTORY SET Image = ? WHERE ItemID = ?", (main_image, ITEM_ID))
                conn.commit()
                conn.close()
                print(f"\n✅ Updated ebayStore.db with image URL")
                
                # Update sold.db
                conn = sqlite3.connect('sold.db')
                cur = conn.cursor()
                cur.execute("UPDATE orders SET image = ?, barcode = ? WHERE item_id = ?", 
                           (main_image, "028851588270", ITEM_ID))
                conn.commit()
                conn.close()
                print(f"✅ Updated sold.db with image URL and barcode")
            else:
                print("\n❌ No PictureURL found in response")
        else:
            print("\n❌ No PictureDetails found in response")
    else:
        errors = root.findall('.//ebay:Errors', ns)
        if errors:
            for error in errors:
                code = error.find('ebay:ErrorCode', ns)
                msg = error.find('ebay:LongMessage', ns)
                print(f"\n❌ Error {code.text if code is not None else 'Unknown'}: {msg.text if msg is not None else 'Unknown'}")
else:
    print(f"❌ HTTP Error: {response.status_code}")
    print(response.text[:500])
