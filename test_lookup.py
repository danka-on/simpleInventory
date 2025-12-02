import requests
import json

# Test the BOL lookup API for a custom item
upc = '777000000013'
response = requests.get(f'http://localhost:5000/api/bol_lookup?upc={upc}')

print(f"Status Code: {response.status_code}")
print(f"Response JSON:")
print(json.dumps(response.json(), indent=2))
