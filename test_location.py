import json
import requests
from token_manager import get_access_token

def create_ebay_location():
    # Get a fresh token
    access_token = get_access_token()
    print(f"Got access token (first 10 chars): {access_token[:10]}...")
    
    # Create headers
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Content-Language": "en-US"
    }
    
    # Create the absolute minimum required payload
    # Based directly on eBay's API example: https://developer.ebay.com/api-docs/sell/inventory/resources/location/methods/createInventoryLocation
    payload = {
        "location": {
            "address": {
                "addressLine1": "123 Test Street",
                "city": "San Jose",
                "stateOrProvince": "CA",
                "postalCode": "95131",
                "country": "US"
            }
        },
        "locationTypes": ["WAREHOUSE"],
        "merchantLocationStatus": "ENABLED"
    }
    
    print("Request payload:")
    print(json.dumps(payload, indent=2))
    
    # Try different location names
    location_ids = ["store1", "warehouse", "loc1", "mylocation"] 
    
    for loc_id in location_ids:
        print(f"\nTrying location ID: {loc_id}")
        
        # Using the correct API path with inventory_location
        url = f"https://api.sandbox.ebay.com/sell/inventory/v1/inventory_location/{loc_id}"
        
        try:
            # Using PUT method as specified in the eBay documentation
            response = requests.put(
                url,
                headers=headers,
                json=payload
            )
            
            print(f"Response status code: {response.status_code}")
            print(f"Response body: {response.text}")
            
            if response.status_code in [200, 201, 204]:
                print(f"SUCCESS! Created location with ID: {loc_id}")
                return loc_id
                
        except Exception as e:
            print(f"Error: {str(e)}")
    
    print("All location creation attempts failed")
    return None

if __name__ == "__main__":
    print("=== eBay Location Creation Test ===")
    location_id = create_ebay_location()
    if location_id:
        print(f"\nSuccessfully created location: {location_id}")
        print("You can now use this location ID in your listings")
    else:
        print("\nFailed to create any location") 