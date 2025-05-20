from contextlib import nullcontext

from flask import Flask, request, send_file, url_for, render_template, jsonify
from PIL import Image, ImageDraw
import io, time , subprocess, os, requests, json, threading, sqlite3
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
import xml.dom.minidom as minidom

from pyasn1_modules.rfc5990 import NullParms

from inventory import find_item  # adjust this to match your actual import
from DBmanager import ebayStoreDB, addToRack
from BOLextractor import process_bol_excel
from manualMatcher import get_bol_items, get_ebay_items, fuse_and_store_match

oldAuth_token = 'v^1.1#i^1#I^3#f^0#p^3#r^1#t^Ul4xMF82OkYwRjY2Q0VFOUY1QUM0MkEyMjkyMDY5Q0E5NjY0NjIxXzFfMSNFXjI2MA=='


CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")
RUNAME = os.getenv("EBAY_RUNAME")
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB limit for uploads
#for ebay api calls

from token_manager import get_access_token, load_tokens, is_expired

access_token = get_access_token()
headers = {
    "Authorization": f"Bearer {access_token}",
    "Content-Type": "application/json"
}

app = Flask(__name__)



@app.route('/extractor')
def extractor():
    return render_template('extractor.html')

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

@app.route("/", methods=["GET", "POST"])
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
        "shelf top": (220, 163, 753, 407),
        "shelf 1":   (90, 825, 930, 990),
        "shelf 2":   (90, 465, 930, 630),
        "shelf 3":   (90, 645, 930, 810),
        "shelf 4":   (90, 825, 930, 990),
        "shelf 5":   (90, 825, 930, 990)
}
'''
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
'''
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

@app.route("/database")
def show_inventory():
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()
    sort = request.args.get('sort', 'ID')
    dir = request.args.get('dir', 'asc')
    allowed_sorts = ['ID','Title','ItemID','SKU','Price','Quantity','List_State','Sold_Date','List_Date']
    if sort not in allowed_sorts:
        sort = 'ID'
    if dir not in ['asc','desc']:
        dir = 'asc'
    conn = sqlite3.connect("ebayStore.db.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    sql = "SELECT * FROM INVENTORY WHERE 1=1"
    params = []
    if q:
        sql += " AND (Title LIKE ? OR ItemID LIKE ? OR SKU LIKE ? OR Price LIKE ? OR Quantity LIKE ? OR List_State LIKE ? OR Sold_Date LIKE ? OR List_Date LIKE ?)"
        for _ in range(8):
            params.append(f"%{q}%")
    if status:
        sql += " AND List_State = ?"
        params.append(status)
    sql += f" ORDER BY {sort} {dir.upper()}"
    cursor.execute(sql, params)
    items = cursor.fetchall()
    conn.close()
    return render_template("inventory.html", items=items)


# inventory flow variables for database injection

same_position = None
position_code = None
barcode = None




@app.route('/additemtrue', methods=['POST'])
def additemtrue():
    #telling database its go time to upload our SHIT

    global same_position
    global position_code
    global barcode
    try:
        print("Flow Complete, adding to Rack....")
        addToRack(position_code, barcode)
        position_code = None
        barcode = None
    except Exception as e:
        print("something went wrong with adding to RACK", e)


    if same_position:
        return render_template("barcode.html")
    else:
        return render_template("position.html")




@app.route("/barcode")
def barcode_page():
    return render_template("barcode.html")

@app.route("/pictures")
def pictures_page():
    return render_template("pictures.html")

#adding inventory flow #1/3
@app.route("/position")
def position_page():

    return render_template("position.html")


#inventory flow #2
@app.route('/submitposition', methods=['POST'])
def process_position():
    global position_code
    position_code = request.form.get('scanned_result')
    print("Received scanned code:", position_code)
    return render_template("barcode.html")


#inventory flow #3
@app.route('/submitbarcode', methods=['POST'])
def process_barcode():
    global barcode
    barcode = request.form.get('scanned_result')
    print("Received scanned code:", barcode)

    return render_template("additem.html")



@app.route('/additem', methods=['POST'])
def additem_page():
    #return pictures

    return render_template("additem.html")


@app.route('/toggle', methods=['POST'])
def toggle():
    global same_position
    data = request.get_json()
    is_checked = data.get('checked', False)
    same_position = is_checked
    print("Checkbox state:", is_checked)  # True or False

    # Respond with JSON so the page doesn't change
    return jsonify({'success': True, 'message': f'Checkbox is {"ON" if is_checked else "OFF"}'})
# order placement flow variables for database injection









import requests

def get_high_res_image_url(url):
    if url and "s-l" in url:
        return url.replace("s-l140.jpg", "s-l1600.jpg").replace("s-l500.jpg", "s-l1600.jpg")
    return url


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
    total_pages = 20
    count = 0
    while page_number <= total_pages:

        xml_payload = f'''
        <?xml version="1.0" encoding="utf-8"?>
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
            <SoldList>
                <Include>true</Include>
                <DurationInDays>60</DurationInDays>
                    <Pagination>
                  <EntriesPerPage>{entries}</EntriesPerPage>
                  <PageNumber>{page_number}</PageNumber>
                </Pagination>
            </SoldList>
            <UnsoldList>
                <Include>true</Include>
                <Pagination>
                      <EntriesPerPage>{entries}</EntriesPerPage>
                      <PageNumber>{page_number}</PageNumber>
                    </Pagination>
                    
                </UnsoldList>
            
            </GetMyeBaySellingRequest>'''

        ns = {'ebay': 'urn:ebay:apis:eBLBaseComponents'}
        response = requests.post("https://api.ebay.com/ws/api.dll", headers=headers, data=xml_payload, timeout=20)
        root = ET.fromstring(response.text)

        '''
        rough_string = ET.tostring(root, encoding="utf-8")
        # Parse that into a minidom object
        dom = minidom.parseString(rough_string)
        # Pretty print
        pretty_xml = dom.toprettyxml(indent="  ")
        print(pretty_xml)
        '''



        ack = root.find('ebay:Ack', ns)
        if ack is None or ack.text != "Success":
            print(f"❌ API Error on page {page_number}: {ack.text if ack is not None else 'No Ack'}")
            #break






        #ACTIVE LIST ITEMS
        ActiveItems = root.findall('.//ebay:ActiveList/ebay:ItemArray/ebay:Item', ns)
        for item in ActiveItems:

            title = item.find('ebay:Title', ns)
            item_id = item.find('ebay:ItemID', ns)
            sku = item.find('ebay:SKU', ns)
            price = item.find('.//ebay:CurrentPrice', ns)
            quantity = item.find('ebay:Quantity', ns)
            list_date = item.find('ebay:ListingDetails/ebay:StartTime', ns)
            sold_date = item.find('ebay:ListingDetails/ebay:EndTime', ns)
            list_state = "Active"
            URL = f"https://www.ebay.com/itm/{item_id.text}"

            picture_url = item.find('.//ebay:PictureDetails/ebay:GalleryURL', ns)
            high_res_url = get_high_res_image_url(picture_url.text) if picture_url is not None else "No image"



            # add to database
            ebayStoreDB(title = title.text if title is not None else "N/A",
                       item_id = item_id.text if item_id is not None else "N/A",
                       sku = sku.text if sku is not None else "None",
                       price = price.text if price is not None else "N/A",
                       quantity = quantity.text if quantity is not None else "N/A",
                       image = high_res_url,
                       List_State = list_state,
                       Sold_Date=sold_date.text if sold_date is not None else "None",
                       List_Date=list_date.text if list_date is not None else "None",
                       URL = URL if URL is not None else "None"
            )




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

        # finding unsold item list
        UnsoldItems = root.findall('.//ebay:UnsoldList/ebay:ItemArray/ebay:Item', ns)
        for item in UnsoldItems:
            title = item.find('ebay:Title', ns)
            item_id = item.find('ebay:ItemID', ns)
            sku = item.find('ebay:SKU', ns)
            price = item.find('.//ebay:CurrentPrice', ns)
            quantity = item.find('ebay:Quantity', ns)
            list_date = item.find('ebay:ListingDetails/ebay:StartTime', ns)
            sold_date = item.find('ebay:ListingDetails/ebay:EndTime', ns)
            list_state = "Unsold"
            URL = f"https://www.ebay.com/itm/{item_id.text}"

            picture_url = item.find('.//ebay:PictureDetails/ebay:GalleryURL', ns)
            high_res_url = get_high_res_image_url(picture_url.text) if picture_url is not None else "No image"

            # add to database
            ebayStoreDB(title=title.text if title is not None else "N/A",
                       item_id=item_id.text if item_id is not None else "N/A",
                       sku=sku.text if sku is not None else "None",
                       price=price.text if price is not None else "N/A",
                       quantity=quantity.text if quantity is not None else "N/A", image=high_res_url,
                       List_State=list_state,
                       Sold_Date=sold_date.text if sold_date is not None else "None",
                       List_Date=list_date.text if list_date is not None else "None",
                       URL=URL if URL is not None else "None"
                       )

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

        # finding sold list items

        SoldItems = root.findall('.//ebay:SoldList/ebay:OrderTransactionArray/ebay:OrderTransaction/ebay:Transaction/ebay:Item', ns)

        for item in SoldItems:
            title = item.find('ebay:Title', ns)
            item_id = item.find('ebay:ItemID', ns)
            sku = item.find('ebay:SKU', ns)
            price = item.find('.//ebay:CurrentPrice', ns)
            quantity = item.find('ebay:Quantity', ns)
            sold_date = item.find('ebay:ListingDetails/ebay:EndTime', ns)
            list_date = item.find('ebay:ListingDetails/ebay:StartTime', ns)
            list_state = "Sold"
            URL = f"https://www.ebay.com/itm/{item_id.text}"
            picture_url = item.find('.//ebay:PictureDetails/ebay:GalleryURL', ns)
            high_res_url = get_high_res_image_url(picture_url.text) if picture_url is not None else "No image"

            # add to database
            ebayStoreDB(title=title.text if title is not None else "N/A",
                       item_id=item_id.text if item_id is not None else "N/A",
                       sku=sku.text if sku is not None else "None",
                       price=price.text if price is not None else "N/A",
                       quantity=quantity.text if quantity is not None else "N/A", image=high_res_url,
                       List_State=list_state,
                       Sold_Date=sold_date.text if sold_date is not None else "None",
                       List_Date=list_date.text if list_date is not None else "None",
                       URL = URL if URL is not None else "None"
                       )

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
                return
                #break  # no pagination info, likely no results

        page_number += 1








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


@app.route('/extractor/upload', methods=['POST'])
def extractor_upload():
    if 'excel_file' not in request.files or 'import_date' not in request.form:
        return jsonify({'success': False, 'error': 'Missing file or import date.'})
    file = request.files['excel_file']
    import_date = request.form['import_date']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected.'})
    if file:
        print('DEBUG: Received file:', file.filename, 'Content-Type:', file.content_type, 'Size:', file.content_length)
        result = process_bol_excel(file, import_date)
        return jsonify(result)
    return jsonify({'success': False, 'error': 'Unknown error during file upload.'})


@app.route('/manualmatcher')
def manualmatcher():
    bol_items = get_bol_items()
    ebay_items = get_ebay_items()
    return render_template('manualmatcher.html', bol_items=bol_items, ebay_items=ebay_items)

@app.route('/match', methods=['POST'])
def match_items():
    data = request.json
    bol_id = data.get('bol_id')
    ebay_id = data.get('ebay_id')
    if bol_id is not None and ebay_id is not None:
        try:
            fuse_and_store_match(bol_id, ebay_id)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})
    return jsonify({'success': False, 'error': 'Missing IDs'})


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

