import requests , os
import base64
import time
import json

from dotenv import load_dotenv
load_dotenv()

CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")
AUTH_CODE = os.getenv("EBAY_AUTH_CODE")  # set this just for one-time use
RUNAME = os.getenv("EBAY_RUNAME")

# Base64 encode your credentials
credentials = f"{CLIENT_ID}:{CLIENT_SECRET}"
encoded_credentials = base64.b64encode(credentials.encode()).decode()

# Setup headers and data for the POST request
headers = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Authorization": f"Basic {encoded_credentials}"
}

data = {
    "grant_type": "authorization_code",
    "code": AUTH_CODE,
    "redirect_uri": RUNAME
}

# Make the request
response = requests.post(
    "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
    headers=headers,
    data=data
)

# Show the result
if response.status_code == 200:
    tokens = response.json()
    tokens["expires_at"] = time.time() + tokens["expires_in"]

    # Save to file
    with open("tokens.json", "w") as f:
        json.dump(tokens, f, indent=2)

    print("✅ Token exchange successful! Tokens saved to tokens.json.")
else:
    print("❌ Failed to exchange token:", response.status_code)
    print(response.text)
