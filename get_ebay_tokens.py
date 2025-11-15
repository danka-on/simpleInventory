import requests
import base64
import getpass

CLIENT_ID = "ilonasco-inventor-PRD-ca6e149a0-10b7daa9"
REDIRECT_URI = "ilonas_corner-ilonasco-invent-vcekryb"
CODE = "v^1.1#i^1#f^0#I^3#p^3#r^1#t^Ul41XzY6MjA2QTIzMzZCRUU1MEE4OTdGREFGOUJFODc4N0U1RTZfMl8xI0VeMjYw"

print("Enter your eBay Production Client Secret (Cert ID):")
CLIENT_SECRET = getpass.getpass()

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

response = requests.post(url, headers=headers, data=data)
print("Status:", response.status_code)
print(response.json())
