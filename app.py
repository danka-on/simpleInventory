from flask import Flask, request, send_file, url_for, render_template, jsonify
from PIL import Image, ImageDraw
import io, time , subprocess, os, requests, json
from dotenv import load_dotenv
from inventory import find_item  # adjust this to match your actual import

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
        policy_ids = {}

        # 1. Fulfillment Policy (Shipping)
        fulfillment_data = {
            "name": "Sample Fulfillment Policy",
            "marketplaceId": "EBAY_US",
            "categoryTypes": [
                {
                    "name": "ALL_EXCLUDING_MOTORS_VEHICLES",
                    "default": True
                }
            ],
            "shippingOptions": [
                {
                    "optionType": "DOMESTIC",
                    "costType": "FLAT_RATE",
                    "shippingServices": [
                        {
                            "sortOrder": 1,
                            "shippingCarrierCode": "USPS",
                            "shippingServiceCode": "USPSPriorityFlatRateBox",
                            "additionalShippingCost": {
                                "value": "0.00",
                                "currency": "USD"
                            },
                            "shippingCost": {
                                "value": "7.00",
                                "currency": "USD"
                            },
                            "freeShipping": False
                        }
                    ]
                }
            ],
            "handlingTime": {
                "unit": "DAY",
                "value": 3
            }
        }
        
        buffer = io.StringIO()
        json.dump(fulfillment_data, buffer, indent=2)
        buffer.seek(0)
        print("Fulfillment Request: ", buffer.read())

        r1 = requests.post(
            "https://api.sandbox.ebay.com/sell/account/v1/fulfillment_policy",
            headers=headers,
            json=fulfillment_data
        )
        
        print("Fulfillment response:", r1.status_code)
        if r1.status_code != 201:
            print("Error creating fulfillment policy:", r1.text)
            return jsonify({"error": f"Failed to create fulfillment policy: {r1.text}"})
            
        fulfillment_id = r1.json()["fulfillmentPolicyId"]
        policy_ids["fulfillmentPolicyId"] = fulfillment_id

        # 2. Payment Policy
        payment_data = {
            "name": "AutoTestPayment",
            "marketplaceId": "EBAY_US",
            "categoryTypes": [
                {
                    "name": "ALL_EXCLUDING_MOTORS_VEHICLES",
                    "default": True
                }
            ],
            "paymentMethods": [
                {
                    "paymentMethodType": "PAYPAL",
                    "recipientAccountReference": {
                        "referenceId": "seller@example.com",
                        "referenceType": "PAYPAL_EMAIL"
                    }
                },
                {
                    "paymentMethodType": "CREDIT_CARD"
                }
            ]
        }

        r2 = requests.post(
            "https://api.sandbox.ebay.com/sell/account/v1/payment_policy",
            headers=headers,
            json=payment_data
        )
        
        print("Payment response:", r2.status_code)
        if r2.status_code != 201:
            print("Error creating payment policy:", r2.text)
            return jsonify({"error": f"Failed to create payment policy: {r2.text}"})
            
        payment_id = r2.json()["paymentPolicyId"]
        policy_ids["paymentPolicyId"] = payment_id

        # 3. Return Policy
        return_data = {
            "name": "AutoTestReturns",
            "marketplaceId": "EBAY_US",
            "categoryTypes": [
                {
                    "name": "ALL_EXCLUDING_MOTORS_VEHICLES",
                    "default": True
                }
            ],
            "returnsAccepted": True,
            "returnPeriod": {"value": "30", "unit": "DAY"},
            "returnShippingCostPayer": "SELLER",
            "refundMethod": "MONEY_BACK"
        }

        r3 = requests.post(
            "https://api.sandbox.ebay.com/sell/account/v1/return_policy",
            headers=headers,
            json=return_data
        )
        
        print("Return response:", r3.status_code)
        if r3.status_code != 201:
            print("Error creating return policy:", r3.text)
            return jsonify({"error": f"Failed to create return policy: {r3.text}"})
            
        return_id = r3.json()["returnPolicyId"]
        policy_ids["returnPolicyId"] = return_id

        # Save policy IDs to file
        try:
            with open("Z:/eBay/policy_ids.json", "w") as f:
                json.dump(policy_ids, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save policy IDs: {e}")

        return jsonify(policy_ids)

    except Exception as e:
        return jsonify({"error": str(e)})

res = requests.get(
    "https://api.sandbox.ebay.com/sell/account/v1/fulfillment_policy?marketplace_id=EBAY_US",
    headers=headers
)
print(res.status_code, res.text)
@app.route("/list-item")
def list_item():
    try:
        access_token = get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        # Load policy IDs
        try:
            with open("Z:/eBay/policy_ids.json", "r") as f:
                policy_ids = json.load(f)
                fulfillment_id = policy_ids.get("fulfillmentPolicyId")
                payment_id = policy_ids.get("paymentPolicyId")
                return_id = policy_ids.get("returnPolicyId")
        except Exception as e:
            return jsonify({"error": f"Could not load policy IDs: {e}"})
            
        # Create merchant location first
        location_data = {
            "location": {
                "address": {
                    "addressLine1": "123 Main Street",
                    "addressLine2": "",
                    "city": "San Jose",
                    "stateOrProvince": "CA",
                    "postalCode": "95131",
                    "country": "US"
                }
            },
            "locationInstructions": "Ring the doorbell",
            "name": "Warehouse-1",
            "merchantLocationStatus": "ENABLED", 
            "locationTypes": ["WAREHOUSE"]
        }
        
        location_res = requests.post(
            "https://api.sandbox.ebay.com/sell/inventory/v1/location",
            headers=headers,
            json=location_data
        )
        
        if location_res.status_code not in [201, 200, 409]:
            print(f"Error creating location: {location_res.status_code}")
            print(location_res.text)
            return jsonify({"error": f"Failed to create location: {location_res.text}"})
        
        location_name = location_data["name"]
        print(f"Location created or already exists: {location_name}")
        
        # Continue with listing
        inventory_data = {
            "availability": {
                "shipToLocationAvailability": {
                    "quantity": 10
                }
            },
            "condition": "NEW",
            "product": {
                "title": "Sample Product",
                "description": "This is a sample product.",
                "aspects": {
                    "Brand": ["Sample Brand"],
                    "Type": ["Sample Type"]
                },
                "imageUrls": [
                    "https://i.imgur.com/abc123.jpg"
                ]
            }
        }
        
        sku = f"sample-sku-{int(time.time())}"
        inventory_url = f"https://api.sandbox.ebay.com/sell/inventory/v1/inventory_item/{sku}"
        
        inventory_res = requests.put(
            inventory_url,
            headers=headers,
            json=inventory_data
        )
        
        if inventory_res.status_code != 201:
            print(f"Error creating inventory: {inventory_res.status_code}")
            print(inventory_res.text)
            return jsonify({"error": f"Failed to create inventory: {inventory_res.text}"})
            
        print(f"Inventory item created: {sku}")
        
        # Create offer
        offer_data = {
            "sku": sku,
            "marketplaceId": "EBAY_US",
            "format": "FIXED_PRICE",
            "availableQuantity": 10,
            "categoryId": "15032",
            "listingDescription": "Sample listing description",
            "listingPolicies": {
                "fulfillmentPolicyId": fulfillment_id,
                "paymentPolicyId": payment_id,
                "returnPolicyId": return_id
            },
            "pricingSummary": {
                "price": {
                    "value": "10.00",
                    "currency": "USD"
                }
            },
            "merchantLocationKey": location_name
        }
        
        offer_res = requests.post(
            "https://api.sandbox.ebay.com/sell/inventory/v1/offer",
            headers=headers,
            json=offer_data
        )
        
        if offer_res.status_code != 201:
            print(f"Error creating offer: {offer_res.status_code}")
            print(offer_res.text)
            return jsonify({"error": f"Failed to create offer: {offer_res.text}"})
            
        offer_id = offer_res.json()["offerId"]
        print(f"Offer created: {offer_id}")
        
        # Publish offer
        publish_res = requests.post(
            f"https://api.sandbox.ebay.com/sell/inventory/v1/offer/{offer_id}/publish",
            headers=headers
        )
        
        if publish_res.status_code != 200:
            print(f"Error publishing offer: {publish_res.status_code}")
            print(publish_res.text)
            return jsonify({"error": f"Failed to publish offer: {publish_res.text}"})
            
        listing_id = publish_res.json()["listingId"]
        
        # Log response
        buffer = io.StringIO()
        json.dump(publish_res.json(), buffer, indent=2)
        buffer.seek(0)
        log_msg = f"Listing published successfully: {buffer.read()}"
        subprocess.run(["echo", log_msg], check=True)
        
        return jsonify({
            "success": True,
            "sku": sku,
            "offerId": offer_id,
            "listingId": listing_id
        })
        
    except Exception as e:
        return jsonify({"error": str(e)})








def run_stuff():
    time.sleep(3)
    start_cloudflare_tunnel()
    print("cloudflare tunnel started")



if __name__ == "__main__":
    run_stuff()
    app.run(host="0.0.0.0", port=8080)


