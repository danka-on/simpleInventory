import requests
import io
import time
import subprocess
import json
from token_manager import get_access_token

url = "https://api.sandbox.ebay.com/sell/account/v1/fulfillment_policy"

# Get a fresh token instead of hardcoded token
access_token = get_access_token()
headers = {
    "Authorization": f"Bearer {access_token}",
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
            "optionType": "DOMESTIC",
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

    # Save the policy ID to file
    with open("Z:/eBay/policy_ids.json", "w+") as f:
        try:
            policies = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            policies = {}
        policies["fulfillmentPolicyId"] = fulfillment_policy_id
        json.dump(policies, f, indent=2)

    # Log with io
    buffer = io.StringIO()
    json.dump(result, buffer, indent=2)
    buffer.seek(0)
    subprocess.run(["echo", f"Fulfillment Policy Response: {buffer.read()[:100]}"], check=True)
except requests.exceptions.RequestException as e:
    print(f"Error: {response.status_code}")
    print(response.text)