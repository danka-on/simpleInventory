from flask import Flask, request, send_file, url_for, render_template, jsonify
from PIL import Image, ImageDraw
import io, time , subprocess, os, requests, json
from dotenv import load_dotenv
from inventory import find_item  # adjust this to match your actual import

CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")
RUNAME = os.getenv("EBAY_RUNAME")

<<<<<<< Updated upstream
app = Flask(__name__)
=======

>>>>>>> Stashed changes
#for ebay api calls

from token_manager import get_access_token, load_tokens, is_expired
access_token = get_access_token()
headers = {
    "Authorization": f"Bearer {access_token}",
    "Content-Type": "application/json"
}

<<<<<<< Updated upstream
=======
app = Flask(__name__)
>>>>>>> Stashed changes

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
<<<<<<< Updated upstream
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
            with open("policy_ids.json", "w") as f:
                json.dump(policy_ids, f, indent=2)
            print(f"Saved policy IDs to policy_ids.json: {json.dumps(policy_ids)}")
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
            "Content-Type": "application/json",
            "Content-Language": "en-US",
            "Accept": "application/json"
        }
        
        # Load policy IDs
        try:
            with open("policy_ids.json", "r") as f:
                policy_ids = json.load(f)
                fulfillment_id = policy_ids.get("fulfillmentPolicyId")
                payment_id = policy_ids.get("paymentPolicyId")
                return_id = policy_ids.get("returnPolicyId")
                print(f"Loaded policy IDs: {json.dumps(policy_ids)}")
        except Exception as e:
            return jsonify({"error": f"Could not load policy IDs: {e}"})
            
        # Create merchant location first - note this is not needed if we've already created it
        location_name = "store1"  # Use the same location name as in create-location
        location_data = {
            "locationTypes": ["WAREHOUSE"],
            "merchantLocationStatus": "ENABLED",
            "location": {
                "address": {
                    "addressLine1": "123 Main St",
                    "city": "San Jose",
                    "stateOrProvince": "CA",
                    "postalCode": "95131",
                    "country": "US"
                }
            }
        }
        
        print(f"Using location: {location_name}")
        
        location_res = requests.put(
            f"https://api.sandbox.ebay.com/sell/inventory/v1/inventory_location/{location_name}",
            headers=headers,
            json=location_data
        )
        
        print(f"Location response: {location_res.status_code}")
        print(location_res.text)
        
        if location_res.status_code not in [201, 200, 204, 409]:
            print(f"Error creating location: {location_res.status_code}")
            print(location_res.text)
            return jsonify({"error": f"Failed to create location: {location_res.text}"})
        
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

@app.route("/create-listing", methods=["POST"])
def create_listing():
    try:
        # Get fresh token
        access_token = get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Content-Language": "en-US",
            "Accept": "application/json"
        }
        
        # Get form data
        data = request.json if request.is_json else request.form
        title = data.get("title", "Sample Item")
        description = data.get("description", "This is a sample item description")
        price = data.get("price", "9.99")
        quantity = int(data.get("quantity", 1))
        category_id = data.get("category_id", "15032")  # Default category
        
        # Try to load policy IDs, or use defaults
        policy_ids_file = "policy_ids.json"
        try:
            if os.path.exists(policy_ids_file):
                with open(policy_ids_file, "r") as f:
                    policy_ids = json.load(f)
                    fulfillment_id = policy_ids.get("fulfillmentPolicyId")
                    payment_id = policy_ids.get("paymentPolicyId")
                    return_id = policy_ids.get("returnPolicyId")
                    print(f"Loaded policy IDs from {policy_ids_file}")
            else:
                print(f"{policy_ids_file} does not exist, proceeding without policy IDs")
                fulfillment_id = data.get("fulfillmentPolicyId")
                payment_id = data.get("paymentPolicyId")
                return_id = data.get("returnPolicyId")
        except Exception as e:
            print(f"Warning: Could not load policy IDs: {e}")
            # We'll continue anyway and let eBay tell us if policies are missing
            fulfillment_id = data.get("fulfillmentPolicyId")
            payment_id = data.get("paymentPolicyId")
            return_id = data.get("returnPolicyId")
        
        # Create inventory item
        sku = f"item-{int(time.time())}"
        aspects = {}
        
        # Extract dynamic item aspects from form data
        for key in data:
            if key.startswith("aspect_"):
                aspect_name = key.replace("aspect_", "")
                aspect_value = data.get(key)
                if aspect_value:
                    # Aspects require array values
                    aspects[aspect_name] = [aspect_value]
        
        # If no aspects provided, add some defaults
        if not aspects:
            aspects = {
                "Brand": ["Sample Brand"],
                "Type": ["Sample Type"]
            }
        
        # Prepare inventory item data
        inventory_data = {
            "availability": {
                "shipToLocationAvailability": {
                    "quantity": quantity
                }
            },
            "condition": "NEW",
            "product": {
                "title": title,
                "description": description,
                "aspects": aspects,
                "imageUrls": []
            }
        }
        
        # Add image URLs if provided
        image_urls = data.getlist("imageUrls[]") if hasattr(data, "getlist") else data.get("imageUrls", "").split(",")
        if image_urls and image_urls[0]:  # Check if we have valid image URLs
            inventory_data["product"]["imageUrls"] = [url.strip() for url in image_urls if url.strip()]
        
        # Create inventory item
        print(f"Creating inventory item with SKU: {sku}")
        inventory_res = requests.put(
            f"https://api.sandbox.ebay.com/sell/inventory/v1/inventory_item/{sku}",
            headers=headers,
            json=inventory_data
        )
        
        if inventory_res.status_code != 201 and inventory_res.status_code != 204:
            error_msg = f"Failed to create inventory: {inventory_res.status_code} - {inventory_res.text}"
            print(error_msg)
            return jsonify({"error": error_msg})
            
        print(f"Inventory item created: {sku}")
        
        # Get or create merchant location
        location_name = "store1"  # Use the same location name as in create-location
        try:
            # First check if we have any inventory locations
            print("Checking for existing merchant locations...")
            location_list_res = requests.get(
                "https://api.sandbox.ebay.com/sell/inventory/v1/inventory_location",
                headers=headers
            )
            if location_list_res.status_code == 200:
                locations = location_list_res.json().get("locations", [])
                if locations:
                    print(f"Found existing locations: {json.dumps(locations)}")
                    location_name = locations[0].get("locationName")
                else:
                    print("No existing locations found, using default location name")
            else:
                print(f"Error checking locations: {location_list_res.status_code}")
        except Exception as e:
            error_msg = f"Warning: Could not check merchant locations: {str(e)}"
            print(error_msg)
            return jsonify({"error": error_msg})
        
        # Create offer data
        offer_data = {
            "sku": sku,
            "marketplaceId": "EBAY_US",
            "format": "FIXED_PRICE",
            "availableQuantity": quantity,
            "categoryId": category_id,
            "listingDescription": description,
            "merchantLocationKey": location_name,
            "pricingSummary": {
                "price": {
                    "currency": "USD",
                    "value": price
                }
            }
        }
        
        # Add listing policies if available
        if all([fulfillment_id, payment_id, return_id]):
            offer_data["listingPolicies"] = {
                "fulfillmentPolicyId": fulfillment_id,
                "paymentPolicyId": payment_id,
                "returnPolicyId": return_id
            }
            print(f"Using policy IDs: fulfillment={fulfillment_id}, payment={payment_id}, return={return_id}")
        else:
            print("No policy IDs provided, eBay will use account defaults if available")
        
        # Create offer
        print("Creating offer...")
        print("Offer data:", json.dumps(offer_data, indent=2))
        
        offer_res = requests.post(
            "https://api.sandbox.ebay.com/sell/inventory/v1/offer",
            headers=headers,
            json=offer_data
        )
        
        if offer_res.status_code != 201 and offer_res.status_code != 204:
            error_msg = f"Failed to create offer: {offer_res.status_code} - {offer_res.text}"
            print(error_msg)
            return jsonify({"error": error_msg})
            
        offer_id = offer_res.json().get("offerId")
        if not offer_id:
            return jsonify({"error": "Offer created but could not get offer ID"})
            
        print(f"Offer created: {offer_id}")
        
        # Publish the offer
        print("Publishing offer...")
        publish_res = requests.post(
            f"https://api.sandbox.ebay.com/sell/inventory/v1/offer/{offer_id}/publish",
            headers=headers
        )
        
        if publish_res.status_code != 200:
            error_msg = f"Failed to publish offer: {publish_res.status_code} - {publish_res.text}"
            print(error_msg)
            return jsonify({"error": error_msg})
            
        listing_id = publish_res.json().get("listingId")
        if not listing_id:
            return jsonify({"error": "Offer published but could not get listing ID"})
            
        print(f"Listing published: {listing_id}")
        
        return jsonify({
            "success": True,
            "message": "Listing created successfully",
            "sku": sku,
            "offerId": offer_id,
            "listingId": listing_id
        })
        
    except Exception as e:
        error_msg = f"Error creating listing: {str(e)}"
        print(error_msg)
        return jsonify({"error": error_msg})
=======
@app.route("/listnames")
def get_inventory_listing_names():
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    endpoint = "https://api.ebay.com/sell/inventory/v1/inventory_item"
    names = []
    limit = 100
    offset = 0

    while True:
        params = {
            "limit": limit,
            "offset": offset
        }
        response = requests.get(endpoint, headers=headers, params=params)
        if response.status_code != 200:
            print("Failed to fetch inventory:", response.status_code, response.text)
            break

        data = response.json()
        for item in data.get("inventoryItems", []):
            product = item.get("product", {})
            title = product.get("title")
            if title:
                names.append(title)

        if "href" in data and "next" in data["href"]:
            offset += limit
        else:
            break

    print("Inventory Listing Names:")
    for name in names:
        print(name)

    return names

>>>>>>> Stashed changes

@app.route("/new-listing")
def new_listing_form():
    return render_template("create_listing.html")

def run_stuff():
    time.sleep(3)
    start_cloudflare_tunnel()
    print("cloudflare tunnel started")

@app.route("/create-location")
def create_location():
    try:
        # Ensure we have a fresh token
        access_token = get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Content-Language": "en-US",
            "Accept": "application/json"
        }
        
        # Remove any sensitive data from debug output
        safe_headers = headers.copy()
        if "Authorization" in safe_headers:
            safe_headers["Authorization"] = "Bearer ****"
        print("Headers:", safe_headers)
        
        # Use a very simple name - this goes in the URL, not the payload
        location_name = "store1"  
        
        # Create a minimalist location payload - note merchantLocationKey is NOT included
        location_data = {
            "locationTypes": ["WAREHOUSE"],
            "merchantLocationStatus": "ENABLED",
            "location": {
                "address": {
                    "addressLine1": "123 Main St",
                    "city": "San Jose",
                    "stateOrProvince": "CA",
                    "postalCode": "95131",
                    "country": "US"
                }
            }
        }
        
        # Print the request payload for debugging
        print("Location payload:", json.dumps(location_data, indent=2))
        print("URL:", f"https://api.sandbox.ebay.com/sell/inventory/v1/inventory_location/{location_name}")
        
        # Make the create request - using PUT not POST as per eBay docs
        response = requests.put(
            f"https://api.sandbox.ebay.com/sell/inventory/v1/inventory_location/{location_name}",
            headers=headers,
            json=location_data
        )
        
        # Print the full response
        print(f"Response status: {response.status_code}")
        print(f"Response headers: {dict(response.headers)}")
        print(f"Response body: {response.text}")
        
        if response.status_code in [200, 201, 204]:
            result = {
                "success": True,
                "status_code": response.status_code,
                "location": location_name,
                "message": "Location created successfully"
            }
        else:
            result = {
                "success": False,
                "status_code": response.status_code,
                "error": response.text
            }
        
        return jsonify(result)
        
    except Exception as e:
        print(f"Exception in create_location: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        })

@app.route("/check-locations")
def check_locations():
    try:
        # Get fresh token
        access_token = get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Content-Language": "en-US",
            "Accept": "application/json"
        }
        
        # Try to get locations list
        print("Making request to check locations...")
        response = requests.get(
            "https://api.sandbox.ebay.com/sell/inventory/v1/inventory_location",
            headers=headers
        )
        
        print(f"Response status: {response.status_code}")
        print(f"Response headers: {response.headers}")
        print(f"Response body: {response.text}")
        
        if response.status_code == 200:
            try:
                data = response.json()
                locations = data.get("locations", [])
                return jsonify({
                    "success": True,
                    "status_code": 200,
                    "count": len(locations),
                    "locations": locations
                })
            except Exception as e:
                print(f"Error parsing locations response: {e}")
                return jsonify({
                    "success": False,
                    "error": f"Error parsing response: {str(e)}",
                    "raw_response": response.text
                })
        else:
            return jsonify({
                "success": False,
                "status_code": response.status_code,
                "error": response.text
            })
            
    except Exception as e:
        print(f"Exception in check_locations: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        })

@app.route("/check-auth")
def check_auth():
    try:
        access_token = get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        # Get user information to verify token
        user_response = requests.get(
            "https://api.sandbox.ebay.com/ws/api.dll",
            headers={
                "X-EBAY-API-SITEID": "0",
                "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
                "X-EBAY-API-CALL-NAME": "GetUser",
                "X-EBAY-API-IAF-TOKEN": access_token,
                "Content-Type": "text/xml"
            },
            data="""<?xml version="1.0" encoding="utf-8"?>
            <GetUserRequest xmlns="urn:ebay:apis:eBLBaseComponents">
                <RequesterCredentials>
                    <eBayAuthToken>{}</eBayAuthToken>
                </RequesterCredentials>
                <DetailLevel>ReturnAll</DetailLevel>
            </GetUserRequest>""".format(access_token)
        )
        
        # Make a simpler request to the inventory API - just get locations count
        inventory_response = requests.get(
            "https://api.sandbox.ebay.com/sell/inventory/v1/location?limit=1",
            headers=headers
        )
        
        # Check if we have a scope issue
        scope_issue = False
        if inventory_response.status_code == 401 or inventory_response.status_code == 403:
            scope_issue = True
            
        # Get current token info
        tokens = load_tokens()
        
        return jsonify({
            "token_valid": True,
            "token_expires_at": tokens.get("expires_at"),
            "current_time": int(time.time()),
            "seconds_until_expiry": int(tokens.get("expires_at", 0) - time.time()),
            "inventory_api_response": {
                "status_code": inventory_response.status_code,
                "body": inventory_response.text
            },
            "user_api_response": {
                "status_code": user_response.status_code,
                "body": user_response.text[:500] + "..." if len(user_response.text) > 500 else user_response.text
            },
            "possible_scope_issue": scope_issue,
            "recommendation": "If you see authorization errors, please ensure your token includes the scopes: sell.inventory, sell.account"
        })
    except Exception as e:
        return jsonify({
            "error": str(e)
        })

if __name__ == "__main__":
    run_stuff()
    app.run(host="0.0.0.0", port=8080)


