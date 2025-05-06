import requests
import io
import time
import subprocess
import json

url = "https://api.sandbox.ebay.com/sell/account/v1/fulfillment_policy"
headers = {
    "Authorization": "Bearer <v^1.1#i^1#I^3#f^0#r^0#p^3#t^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>",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Content-Language": "en-US"
}
payload = {
    "name": "Sample Fulfillment Policy",
    "marketplaceId": "EBAY_US",
    "categoryTypes": [{"name": "ALL_EXCLUDING_MOTORS_VEHICLES", "default": True}],
    "shippingOptions": [
        {
            "costType": "FLAT_RATE",
            "shippingServices": [
                {
                    "sortOrder": 1,
                    "shippingCarrierCode": "USPS",
                    "shippingServiceCode": "USPSPriorityFlatRateBox",
                    "additionalShippingCost": {"value": "0.00", "currency": "USD"},
                    "shippingCost": {"value": "7.00", "currency": "USD"},
                    "freeShipping": False
                }
            ]
        }
    ],
    "handlingTime": {"unit": "DAY", "value": 3}
}

try:
    time.sleep(1)  # Rate limiting
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    result = response.json()
    fulfillment_policy_id = result.get("fulfillmentPolicyId")
    print(f"Fulfillment Policy ID: {fulfillment_policy_id}")

    # Log with io
    buffer = io.StringIO()
    json.dump(result, buffer, indent=2)
    buffer.seek(0)
    subprocess.run(["echo", f"Fulfillment Policy Response: {buffer.read()[:100]}"], check=True)
except requests.exceptions.RequestException as e:
    print(f"Error: {response.json()}")