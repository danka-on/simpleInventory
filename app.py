from contextlib import nullcontext

from flask import Flask, request, send_file, url_for, render_template, jsonify
from PIL import Image, ImageDraw
import io, time , subprocess, os, requests, json, threading
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
from inventory import find_item  # adjust this to match your actual import

oldAuth_token = 'v^1.1#i^1#I^3#f^0#p^3#r^1#t^Ul4xMF82OkYwRjY2Q0VFOUY1QUM0MkEyMjkyMDY5Q0E5NjY0NjIxXzFfMSNFXjI2MA=='


CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")
RUNAME = os.getenv("EBAY_RUNAME")
app = Flask(__name__)
#for ebay api calls

from token_manager import get_access_token, load_tokens, is_expired

access_token = get_access_token()
headers = {
    "Authorization": f"Bearer {access_token}",
    "Content-Type": "application/json"
}

app = Flask(__name__)

@app.route("/token-status")
def token_status():
    try:
        tokens = load_tokens()
        expired = is_expired(tokens)
        access_token = get_access_token()  # this will refresh if expired

        return jsonify({
            "token_expired": expired,
            "access_token": access_token[:40] + "...",  # for safety
            "expires_at": tokens.get("expires_at"),
            "current_time": int(time.time()),
            "seconds_until_expiry": int(tokens.get("expires_at") - time.time())
        })

    except Exception as e:
        return jsonify({"error": str(e)})

def start_cloudflare_tunnel():
    subprocess.Popen([
        "cloudflared",
        "tunnel",
        "--config",
        "C:\\Users\\boxatron\\.cloudflared\\config.yml",
        "run",
        "mytunnel"
    ])

@app.route("/")
def home():
    shelf = request.args.get("shelf")
    return render_template("index.html", shelf=shelf)


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "Authorization failed or cancelled."

    # Step 5: exchange this code for an access token
    return f"Authorization code: {code}"
@app.route("/privacy")
def privacy():
    return "<h1>Privacy Policy</h1><p>No user data is stored or shared. This app is for sandbox testing only.</p>"



@app.route("/search", methods=["POST"])
def search():
    query = request.form["query"]
    result = find_item(query)
    return render_template("index.html", search_result=result)

SHELF_COORDS = {
    "Hammer_Rack": {
        "shelf0": (220, 163, 753, 407),
        "shelf1":   (90, 825, 930, 990),
        "shelf2":   (90, 465, 930, 630),
        "shelf3":   (90, 645, 930, 810),
        "shelf4":   (90, 825, 930, 990),
        "shelf5":   (90, 825, 930, 990)
    },
    "Wheel_Rack": {
        "shelf0": (220, 163, 753, 407),
        "shelf1":   (90, 825, 930, 990),
        "shelf2":   (90, 465, 930, 630),
        "shelf3":   (90, 645, 930, 810),
        "shelf4":   (90, 825, 930, 990),
        "shelf5":   (90, 825, 930, 990)
    },
    "Sun_Rack": {
        "shelf0": (220, 163, 753, 407),
        "shelf1":   (90, 825, 930, 990),
        "shelf2":   (90, 465, 930, 630),
        "shelf3":   (90, 645, 930, 810),
        "shelf4":   (90, 825, 930, 990),
        "shelf5":   (90, 825, 930, 990)
    },
    "Plane_Rack": {
        "shelf0": (220, 163, 753, 407),
        "shelf1":   (90, 825, 930, 990),
        "shelf2":   (90, 465, 930, 630),
        "shelf3":   (90, 645, 930, 810),
        "shelf4":   (90, 825, 930, 990),
        "shelf5":   (90, 825, 930, 990)
    },
    "Bike_Rack": {
        "shelf0": (220, 163, 753, 407),
        "shelf1":   (90, 825, 930, 990),
        "shelf2":   (90, 465, 930, 630),
        "shelf3":   (90, 645, 930, 810),
        "shelf4":   (90, 825, 930, 990),
        "shelf5":   (90, 825, 930, 990)
    }
}

@app.route("/highlight")
def highlight():
    shelf_name = request.args.get("shelf", "").lower()

    # Load original image
    image_path = "static/shelf1.png"  # move your base image here
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Draw the selected shelf in green
    if shelf_name in SHELF_COORDS:
        draw.rectangle(SHELF_COORDS[shelf_name], outline="green", width=30)

    # Output to memory, not file
    img_io = io.BytesIO()
    img.save(img_io, "PNG")
    img_io.seek(0)

    return send_file(img_io, mimetype="image/png")

####################################

import requests

def get_high_res_image_url(url):
    if url and "s-l" in url:
        return url.replace("s-l140.jpg", "s-l1600.jpg").replace("s-l500.jpg", "s-l1600.jpg")
    return url

#@app.route("/orders" , methods=["POST"])
def orders():
    print("✅ Now running orders()...")
    headers = {
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": "GetMyeBaySelling",
        "X-EBAY-API-DEV-NAME": os.getenv("EBAY_PROD_DEV_ID"),
        "X-EBAY-API-APP-NAME": os.getenv("EBAY_PROD_APP_ID"),
        "X-EBAY-API-CERT-NAME": os.getenv("EBAY_PROD_CERT_ID"),
        "Content-Type": "text/xml"
    }
    entries = 100
    page_number = 1
    total_pages = 1
    count = 1
    while page_number <= total_pages:

        xml_payload = f'''<?xml version="1.0" encoding="utf-8"?>
        <GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
          <RequesterCredentials>
            <eBayAuthToken>{os.getenv("EBAY_OLDAUTH_TOKEN")}</eBayAuthToken>
          </RequesterCredentials>
          <ErrorLanguage>en_US</ErrorLanguage>
          <WarningLevel>High</WarningLevel>
          <ActiveList>
            <Sort>TimeLeft</Sort>
            <Pagination>
              <EntriesPerPage>{entries}</EntriesPerPage>
              <PageNumber>{page_number}</PageNumber>
            </Pagination>
          </ActiveList>
        <UnsoldList>
                <Include>true</Include>
            </UnsoldList>
        </GetMyeBaySellingRequest>'''

        ns = {'ebay': 'urn:ebay:apis:eBLBaseComponents'}
        response = requests.post("https://api.ebay.com/ws/api.dll", headers=headers, data=xml_payload, timeout=20)
        root = ET.fromstring(response.text)

        ack = root.find('ebay:Ack', ns)
        if ack is None or ack.text != "Success":
            print(f"❌ API Error on page {page_number}: {ack.text if ack is not None else 'No Ack'}")
            break

        #SoldList = root.findall('.//ebay:SoldList', ns)

        #print("soldlist stats",SoldList.tags, SoldList.attributes)

        items = root.findall('.//ebay:Item', ns)
        for item in items:

            title = item.find('ebay:Title', ns)
            item_id = item.find('ebay:ItemID', ns)
            sku = item.find('ebay:SKU', ns)
            price = item.find('.//ebay:CurrentPrice', ns)
            quantity = item.find('ebay:Quantity', ns)

            picture_url = item.find('.//ebay:PictureDetails/ebay:GalleryURL', ns)
            high_res_url = get_high_res_image_url(picture_url.text) if picture_url is not None else "No image"




            print("Item", count)
            print("📦 Title:", title.text if title is not None else "N/A")
            print("🆔 ItemID:", item_id.text if item_id is not None else "N/A")
            print("🔖 SKU:", sku.text if sku is not None else "None")
            print("💲 Price:", price.text if price is not None else "N/A")
            print("🔢 Quantity:", quantity.text if quantity is not None else "N/A")
            print("🖼️ Image:", high_res_url)
            print("🛣️ URL:", f"https://www.ebay.com/itm/{item_id.text}")
            print("—" * 40)
            count += 1

        # Get total pages if first time
        if page_number == 1:
            page_info = root.find('.//ebay:PaginationResult', ns)
            if page_info is not None:
                total_pages = int(page_info.find('ebay:TotalNumberOfPages', ns).text)
            else:
                break  # no pagination info, likely no results

        page_number += 1




    #items = root.findall('.//ebay:Item', ns)

'''
    <ActiveList>
    <SoldList>
    <UnsoldList>

    < SoldList > ItemListCustomizationType
    < DurationInDays > int < / DurationInDays >
    < Include > boolean < / Include >
    < IncludeNotes > boolean < / IncludeNotes >
    < OrderStatusFilter > OrderStatusFilterCodeType < / OrderStatusFilter >
    < Pagination > PaginationType
    < EntriesPerPage > int < / EntriesPerPage >
    < PageNumber > int < / PageNumber >

< / Pagination >
< Sort > ItemSortTypeCodeType < / Sort >
< / SoldList >
< UnsoldList > ItemListCustomizationType
< DurationInDays > int < / DurationInDays >
< Include > true < / Include >
< IncludeNotes > false < / IncludeNotes >
< Pagination > PaginationType
< EntriesPerPage > int < / EntriesPerPage >
< PageNumber > int < / PageNumber >
< / Pagination >
< Sort > ItemSortTypeCodeType < / Sort >
< / UnsoldList >
< / GetMyeBaySellingRequest >


    
    # Print all elements and their text
    def strip_ns(tag):
        return tag.split('}', 1)[-1] if '}' in tag else tag

    unique_tags = set()

    for elem in root.iter():
        unique_tags.add(strip_ns(elem.tag))

    print("Unique XML tags:")
    for tag in sorted(unique_tags):
        print(tag)




        
    for item in items:
        item_id = item.find('ebay:ItemID', ns).text
        title = item.find('ebay:Title', ns).text
        print(f"{item_id} - {title}")
        
    return response.status_code #response.text





def run_stuff():


    time.sleep(3)
    start_cloudflare_tunnel()




if __name__ == "__main__":
    run_stuff()
    print("running host")
    app.run(host="0.0.0.0", port=8080)

'''






def start_flask():
    app.run(host="0.0.0.0", port=8080)

def start_tunnel():
    subprocess.Popen([
        "cloudflared",
        "tunnel",
        "--config",
        "C:\\Users\\boxatron\\.cloudflared\\config.yml",
        "run",
        "mytunnel"
    ])
    print("⏳ Cloudflare tunnel starting...")
    time.sleep(3)
    try:
        res = requests.get("http://localhost:5555/metrics")
        for line in res.text.splitlines():
            if "userURL" in line:
                public_url = line.split(" ")[-1]
                print(f"✅ Public URL: {public_url}")
                break
    except Exception as e:
        print("❌ Tunnel metrics not found:", e)


if __name__ == "__main__":
    # Start Flask in a thread
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # Wait a bit, then start tunnel
    time.sleep(1)
    start_tunnel()

    # Wait until both are likely up
    time.sleep(2)
    orders()

    # Keep main thread alive
    while True:
        time.sleep(1)


'''

    headers = {
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": "GetSellerList",
        "X-EBAY-API-DEV-NAME": os.getenv("EBAY_PROD_DEV_ID"),
        "X-EBAY-API-APP-NAME": os.getenv("EBAY_PROD_APP_ID"),
        "X-EBAY-API-CERT-NAME": os.getenv("EBAY_PROD_CERT_ID"),
        "Content-Type": "text/xml"
               }


    #payload variables

    search_detail = "Coarse"
    start_date = "2025-01-10T06:38:48.420Z"
    end_date = "2025-05-10T06:38:48.420Z"
    entries = '200'
    #83 items in 5-1 , 5-10
    page_number = 1

    xml_payload = f
    <?xml version="1.0" encoding="utf-8"?>
    <GetSellerListRequest xmlns="urn:ebay:apis:eBLBaseComponents">
    <RequesterCredentials>
    <eBayAuthToken>{os.getenv("EBAY_OLDAUTH_TOKEN")}</eBayAuthToken>
    </RequesterCredentials>
	<ErrorLanguage>en_US</ErrorLanguage>
	<WarningLevel>High</WarningLevel>
     <!--You can use DetailLevel or GranularityLevel in a request, but not both-->
    <GranularityLevel>{search_detail}</GranularityLevel>
     <!-- Enter a valid Time range to get the Items listed using this format
          2013-03-21T06:38:48.420Z -->
    <StartTimeFrom>{start_date}</StartTimeFrom>
    <StartTimeTo>{end_date}</StartTimeTo>
    <IncludeWatchCount>true</IncludeWatchCount>
    <Pagination>
        <PageNumber>{page_number}</PageNumber>
        <EntriesPerPage>{entries}</EntriesPerPage>
    </Pagination>
    </GetSellerListRequest>
    
    response = requests.post("https://api.ebay.com/ws/api.dll", headers=headers, data=xml_payload)
    root = ET.fromstring(response.text)

    ns = {'ebay': 'urn:ebay:apis:eBLBaseComponents'}

    items = root.findall('.//ebay:Item', ns)

    count = 0
    for item in items:
        title = item.find('ebay:Title', ns)
        if title is not None:
         count += 1
         print(count," ", title.text)
    print("what is happening?")
    
    '''