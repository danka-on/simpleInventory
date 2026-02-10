# SP-API Shipping Class - Internal Request Mechanism Analysis

## Overview
The `Shipping` class from `python-amazon-sp-api` (v1.9.50) is a thin wrapper around the Client base class that handles the SP-API shipping endpoints. All actual HTTP request logic is delegated to the base `Client` class.

## 1. Class Hierarchy & Request Method

### Shipping Class
- **Location**: `sp_api/api/shipping/shipping.py`
- **Inherits from**: `Client` (from `sp_api.base.client.Client`)
- **Request method used**: `self._request(path, **kwargs)` - inherited from `Client` class

### Base Client Class
- **Location**: `sp_api/base/client.py`
- **Parent**: `BaseClient` (from `sp_api.base.base_client.BaseClient`)
- **Core request implementation**: The `_request()` method in `Client` class

## 2. Request Mechanism: How Shipping Makes HTTP Requests

### The _request() Method (Client class)

The `_request()` method signature:
```
def _request(self, path: str, *, data: dict = None, params: dict = None, 
             headers=None, add_marketplace=True, res_no_data: bool = False, 
             bulk: bool = False, wrap_list: bool = False) -> ApiResponse
```

**Key flow:**
1. Path is constructed: `self.endpoint + self._check_version(path)`
2. HTTP method is extracted from params or data (defaults to GET)
3. Marketplace ID is automatically added to request (if add_marketplace=True)
4. Logging is performed
5. Actual HTTP request is made using the requests library

The actual HTTP call uses the requests library:
```
res = request(
    self.method,
    self.endpoint + self._check_version(path),
    params=params,
    data=json.dumps(data) if data and method in POST/PUT/PATCH else None,
    headers=headers or self.headers,
    timeout=self.timeout,
    proxies=self.proxies,
    verify=self.verify,
)
```

### Key Details:

**Library Used**: requests library (standard Python HTTP client)

**Headers provided by Client class**:
- host: extracted from endpoint
- user-agent: "python-sp-api-1.9.50"
- x-amz-access-token: OAuth access token (required for auth)
- x-amz-date: UTC datetime in format YYYYMMDDTHHMMSSz
- content-type: "application/json"

**Base URL Pattern**:
- Production: https://sellingpartnerapi-{region}.amazon.com
  - North America (US/CA/MX/BR): https://sellingpartnerapi-na.amazon.com
  - Europe (EU countries): https://sellingpartnerapi-eu.amazon.com
  - Far East (JP/SG/AU): https://sellingpartnerapi-fe.amazon.com
- Sandbox: https://sandbox.sellingpartnerapi-{region}.amazon.com

## 3. Custom HTTP Requests Through SP-API Auth System

### Option 1: Direct _request() Method (RECOMMENDED)

You can call _request() directly on a Shipping instance to make custom requests:

```python
from sp_api.api.shipping import Shipping

shipping = Shipping(refresh_token="...", account="default")

# Make a custom GET request
response = shipping._request(
    "/shipping/v1/custom-endpoint",
    params={"param1": "value1"},
)

# Make a custom POST request
response = shipping._request(
    "/shipping/v1/custom-endpoint",
    data={"key": "value"},
    params={"method": "POST"}
)
```

Benefits of _request():
- Automatically handles OAuth token management
- Automatically adds auth headers (x-amz-access-token)
- Automatically adds marketplace ID (if needed)
- Handles response parsing
- Handles errors consistently

### Option 2: Grantless Operations

For operations that don't require a refresh token:

```python
response = shipping._request_grantless_operation(
    path="/shipping/v1/grantless-endpoint",
    data={"key": "value"},
    params={"method": "POST"}
)
```

### Option 3: Raw requests Library (Not Recommended)

Access the auth token directly:

```python
shipping = Shipping(refresh_token="...", account="default")

# Get the access token
auth_response = shipping.auth
access_token = auth_response.access_token

# Make manual request
import requests
response = requests.post(
    f"{shipping.endpoint}/shipping/v1/custom-endpoint",
    json={"key": "value"},
    headers={
        "x-amz-access-token": access_token,
        "x-amz-date": datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"),
        "content-type": "application/json",
    }
)
```

## 4. Endpoint Patterns & Base URLs

### Base Endpoints by Marketplace

Depends on marketplace (set during Client initialization):

```python
shipping = Shipping(
    marketplace=Marketplaces.US,
    refresh_token="..."
)
```

This sets shipping.endpoint to one of:
- North America: https://sellingpartnerapi-na.amazon.com
- Europe: https://sellingpartnerapi-eu.amazon.com
- Far East: https://sellingpartnerapi-fe.amazon.com

### Shipping API Endpoints

All endpoints start with /shipping/v1/:

- POST   /shipping/v1/shipments              - Create shipment
- GET    /shipping/v1/shipments/{id}         - Get shipment details
- POST   /shipping/v1/shipments/{id}/cancel  - Cancel shipment
- POST   /shipping/v1/shipments/{id}/purchaseLabels - Buy labels
- POST   /shipping/v1/shipments/{id}/label   - Retrieve label
- POST   /shipping/v1/purchaseShipment       - One-shot purchase
- POST   /shipping/v1/rates                  - Get rates
- GET    /shipping/v1/account                - Verify account
- GET    /shipping/v1/tracking/{id}          - Get tracking info

### Full URL Format Example
https://sellingpartnerapi-na.amazon.com/shipping/v1/shipments

## 5. Implementation Example

```python
from sp_api.api.shipping import Shipping
from sp_api.base import Marketplaces

shipping = Shipping(
    marketplace=Marketplaces.US,
    refresh_token=your_refresh_token,
    account="default"
)

# Use built-in method
response = shipping.get_account()

# Custom request through _request
custom_response = shipping._request(
    "/shipping/v1/account",
    params={"method": "GET"}
)

# POST custom data
shipment_data = {
    "clientReferenceId": "test",
    "shipTo": {
        "name": "John Doe",
        "addressLine1": "123 Main St",
        "city": "New York",
        "stateOrRegion": "NY",
        "postalCode": "10001",
        "countryCode": "US"
    },
    "shipFrom": {
        "name": "Business",
        "addressLine1": "456 Ave",
        "city": "Los Angeles",
        "stateOrRegion": "CA",
        "postalCode": "90001",
        "countryCode": "US"
    },
    "containers": []
}

response = shipping._request(
    "/shipping/v1/shipments",
    data=shipment_data,
    params={"method": "POST"}
)
```

## 6. Summary

Base class method: _request() from Client class
HTTP library: requests
Auth: OAuth 2.0 with x-amz-access-token header
Custom requests: Yes - call _request() directly
Base URL: https://sellingpartnerapi-{region}.amazon.com
Shipping prefix: /shipping/v1/
Automatic marketplace handling: Yes
Token refresh: Automatic
Error handling: Yes

## Notes

1. Token Expiration: Client automatically handles token refresh. Each call to shipping.auth will refresh if needed.

2. Marketplace-specific: Initialize with correct marketplace:
   ```python
   shipping = Shipping(marketplace=Marketplaces.CA)
   ```

3. Rate Limits: SP-API has rate limits per operation.

4. Sandbox Testing: Use AWS_ENV=SANDBOX environment variable.

5. Data Validation: _request() does NOT validate schemas. Check Amazon's API docs.
