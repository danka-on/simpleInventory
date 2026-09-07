import requests
import base64
import json
import time
import os
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "ilonasco-inventor-PRD-ca6e149a0-10b7daa9")
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "PRD-a6e149a03f96-16c6-4cc3-8983-0a78")
REDIRECT_URI = "ilonas_corner-ilonasco-invent-vcekryb"

CODE = input("Paste your eBay authorization code and press Enter: ").strip()
print(f"\nCode starts with: {CODE[:20]}...")
print(f"Code length: {len(CODE)}")
print(f"Code contains ^: {'^' in CODE}")

url = "https://api.ebay.com/identity/v1/oauth2/token"
headers = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Authorization": "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
}
data = {
    "grant_type": "authorization_code",
    "code": CODE,
    "redirect_uri": REDIRECT_URI
}

response = requests.post(url, headers=headers, data=data, timeout=30)
print("Status:", response.status_code)
result = response.json()
print(json.dumps(result, indent=2))

if response.status_code == 200:
    result["expires_at"] = time.time() + result["expires_in"]
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tokens.json"), "w") as f:
        json.dump(result, f, indent=2)
    print("\ntokens.json updated successfully!")
else:
    print("\nFailed - generate a new authorization code and try again immediately.")
