from flask import Flask, request, send_file, url_for, render_template, jsonify
from PIL import Image, ImageDraw
import io, time , subprocess, os, requests
from dotenv import load_dotenv
from inventory import find_item  # adjust this to match your actual import

CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")
RUNAME = os.getenv("EBAY_RUNAME")
app = Flask(__name__)
#for ebay api calls
from token_manager import get_access_token, load_tokens, is_expired
access_token = get_access_token()
HEADERS = {
    "Authorization": f"Bearer {access_token}",
    "Content-Type": "application/json"
}
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
@app.route("/create-policies")
def create_policies():
    try:
        access_token = get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        # 1. Fulfillment Policy (Shipping)
        fulfillment_data = {
            "name": "AutoTestFulfillment",
            "marketplaceId": "EBAY_US",
            "shippingOptions": [{
                "shippingServices": [{
                    "shippingServiceCode": "USPSFirstClass",
                    "freeShipping": True
                }],
                "optionType": "DOMESTIC"
            }]
        }

        r1 = requests.post(
            "https://api.sandbox.ebay.com/sell/account/v1/fulfillment_policy",
            headers=headers,
            json=fulfillment_data
        )
        print("Fulfillment response:", r1.status_code, r1.text)
        fulfillment_id = r1.json()["fulfillmentPolicyId"]

        # 2. Payment Policy
        payment_data = {
            "name": "AutoTestPayment",
            "marketplaceId": "EBAY_US",
            "paymentMethods": ["CREDIT_CARD"]
        }

        r2 = requests.post(
            "https://api.sandbox.ebay.com/sell/account/v1/payment_policy",
            headers=headers,
            json=payment_data
        )
        print("Payment response:", r2.status_code, r2.text)
        payment_id = r2.json()["paymentPolicyId"]

        # 3. Return Policy
        return_data = {
            "name": "AutoTestReturns",
            "marketplaceId": "EBAY_US",
            "returnsAccepted": True,
            "returnMethod": "EXCHANGE",
            "returnPeriod": {"value": "30", "unit": "DAY"},
            "refundMethod": "MONEY_BACK"
        }

        r3 = requests.post(
            "https://api.sandbox.ebay.com/sell/account/v1/return_policy",
            headers=headers,
            json=return_data
        )
        print("Return response:", r3.status_code, r3.text)
        return_id = r3.json()["returnPolicyId"]

        return jsonify({
            "fulfillmentPolicyId": fulfillment_id,
            "paymentPolicyId": payment_id,
            "returnPolicyId": return_id
        })

    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/list-item")
def list_item():
    try:
        access_token = get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        # === Replace with your policy IDs from sandbox ===
        fulfillment_policy_id = "REPLACE_ME"
        payment_policy_id = "REPLACE_ME"
        return_policy_id = "REPLACE_ME"

        sku = "test-sku-001"
        title = "Sandbox Widget"
        quantity = 3

        # 1. Upload inventory item
        inventory_payload = {
            "product": {
                "title": title,
                "description": "A test item in the sandbox environment",
                "aspects": {"Brand": ["FakeBrand"]}
            },
            "availability": {
                "shipToLocationAvailability": {"quantity": quantity}
            },
            "condition": "NEW"
        }

        requests.put(
            f"https://api.sandbox.ebay.com/sell/inventory/v1/inventory_item/{sku}",
            headers=headers,
            json=inventory_payload
        )

        # 2. Create offer
        offer_payload = {
            "sku": sku,
            "marketplaceId": "EBAY_US",
            "format": "FIXED_PRICE",
            "availableQuantity": quantity,
            "categoryId": "9355",  # Cell Phones category
            "listingPolicies": {
                "fulfillmentPolicyId": fulfillment_policy_id,
                "paymentPolicyId": payment_policy_id,
                "returnPolicyId": return_policy_id
            },
            "pricingSummary": {
                "price": {"value": "9.99", "currency": "USD"}
            }
        }

        res = requests.post(
            "https://api.sandbox.ebay.com/sell/inventory/v1/offer",
            headers=headers,
            json=offer_payload
        )

        offer_id = res.json().get("offerId")

        # 3. Publish the offer
        requests.post(
            f"https://api.sandbox.ebay.com/sell/inventory/v1/offer/{offer_id}/publish",
            headers=headers
        )

        return jsonify({"message": "✅ Item listed successfully!", "sku": sku, "offer_id": offer_id})

    except Exception as e:
        return jsonify({"error": str(e)})










def run_stuff():
    time.sleep(3)
    start_cloudflare_tunnel()
    print("cloudflare tunnel started")



if __name__ == "__main__":
    run_stuff()
    app.run(host="0.0.0.0", port=8080)


