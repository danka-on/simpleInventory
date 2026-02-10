# SP-API Shipping Class - Quick Summary

## Installation Location
```
C:\Users\boxatron\Desktop\simpleInventory\.venv\Lib\site-packages\sp_api\
```

Version: python-amazon-sp-api 1.9.50

## Question 1: How does Shipping class make requests?

**Base class method used:** `Client._request()`

**File:** `sp_api/base/client.py` (lines 83-127)

**How it works:**
1. Shipping methods are decorated with `@sp_endpoint(path, method="GET/POST")`
2. Methods call `self._request(path, data=kwargs)` or `self._request(path, params=kwargs)`
3. `_request()` uses the `requests` library to make HTTP calls
4. Automatically adds OAuth token in `x-amz-access-token` header

**The actual HTTP call:**
```python
from requests import request

res = request(
    self.method,                              # HTTP method (GET, POST, etc.)
    self.endpoint + self._check_version(path), # Full URL
    params=params,                            # Query params
    data=json.dumps(data) if data else None,  # JSON body
    headers=headers or self.headers,          # Auth headers with token
    timeout=self.timeout,
    proxies=self.proxies,
    verify=self.verify,
)
```

## Question 2: Custom HTTP requests through sp_api auth system?

**YES - Use `_request()` directly**

```python
from sp_api.api.shipping import Shipping
from sp_api.base import Marketplaces

shipping = Shipping(
    marketplace=Marketplaces.US,
    refresh_token="your_token"
)

# Custom GET request
response = shipping._request(
    "/shipping/v1/custom-endpoint",
    params={"custom_param": "value"}
)

# Custom POST request
response = shipping._request(
    "/shipping/v1/custom-endpoint",
    data={"key": "value"},
    params={"method": "POST"}
)
```

**What you get automatically:**
- OAuth token in headers (x-amz-access-token)
- Marketplace ID auto-added
- Response parsing (JSON -> ApiResponse)
- Error handling (raises exceptions)
- Token refresh (automatic)

**Alternative for grantless operations:**
```python
response = shipping._request_grantless_operation(
    path="/shipping/v1/endpoint",
    data={"key": "value"}
)
```

## Question 3: Base URL / Endpoint patterns?

**Base URLs by Region:**
- North America: `https://sellingpartnerapi-na.amazon.com` (US, CA, MX, BR)
- Europe: `https://sellingpartnerapi-eu.amazon.com` (DE, FR, GB, IT, ES, etc.)
- Far East: `https://sellingpartnerapi-fe.amazon.com` (JP, SG, AU)

**Shipping Endpoints (all start with `/shipping/v1/`):**
- POST `/shipping/v1/shipments` - Create shipment
- GET `/shipping/v1/shipments/{shipmentId}` - Get details
- POST `/shipping/v1/shipments/{shipmentId}/cancel` - Cancel
- POST `/shipping/v1/shipments/{shipmentId}/purchaseLabels` - Buy labels
- POST `/shipping/v1/shipments/{shipmentId}/label` - Retrieve label
- POST `/shipping/v1/purchaseShipment` - One-shot purchase
- POST `/shipping/v1/rates` - Get rates
- GET `/shipping/v1/account` - Verify account
- GET `/shipping/v1/tracking/{trackingId}` - Tracking info

**Full URL Example:**
```
https://sellingpartnerapi-na.amazon.com/shipping/v1/shipments
```

## Key Files Reference

| File | Purpose |
|------|---------|
| `sp_api/api/shipping/shipping.py` | Shipping class with all API methods |
| `sp_api/base/client.py` | Client class with _request() method |
| `sp_api/base/base_client.py` | BaseClient (parent class) |
| `sp_api/base/marketplaces.py` | Marketplace endpoints config |
| `sp_api/base/ApiResponse.py` | Response object structure |

## Request Flow Diagram

```
User calls: shipping.get_account()
    |
    v
Shipping.get_account() (decorated with @sp_endpoint)
    |
    v
Calls: self._request("/shipping/v1/account", params=kwargs)
    |
    v
Client._request() method:
    - Extracts HTTP method (GET)
    - Adds marketplace ID
    - Gets auth headers (x-amz-access-token)
    - Calls requests.request()
    |
    v
requests library makes HTTP call to:
https://sellingpartnerapi-na.amazon.com/shipping/v1/account
    |
    v
Response parsed by _check_response()
    |
    v
Returns: ApiResponse object
```

## Authentication Headers (Auto-Added)

```python
{
    "host": "sellingpartnerapi-na.amazon.com",
    "user-agent": "python-sp-api-1.9.50",
    "x-amz-access-token": "<OAuth 2.0 access token>",
    "x-amz-date": "20250209T120000Z",
    "content-type": "application/json",
}
```

The OAuth token is automatically refreshed when needed via `Client.auth` property.

## Summary Table

| Aspect | Answer |
|--------|--------|
| **Base class method** | `Client._request()` from `sp_api/base/client.py` |
| **HTTP library** | `requests` (standard Python) |
| **Auth mechanism** | OAuth 2.0 via `x-amz-access-token` header |
| **Custom requests** | Yes - call `shipping._request(path, data=..., params=...)` |
| **Base URL pattern** | `https://sellingpartnerapi-{region}.amazon.com` |
| **Shipping path prefix** | `/shipping/v1/` |
| **Automatic marketplace** | Yes - via `_add_marketplaces()` |
| **Token refresh** | Automatic via `Client._auth.get_auth()` |
| **Error handling** | Exceptions raised via `_check_response()` |

## Example: Full Custom Request

```python
from sp_api.api.shipping import Shipping
from sp_api.base import Marketplaces

shipping = Shipping(
    marketplace=Marketplaces.US,
    refresh_token="your_refresh_token"
)

# Custom request with full control
response = shipping._request(
    "/shipping/v1/shipments",
    data={
        "clientReferenceId": "test-123",
        "shipTo": {
            "name": "John Doe",
            "addressLine1": "123 Main St",
            "city": "New York",
            "stateOrRegion": "NY",
            "postalCode": "10001",
            "countryCode": "US"
        },
        "shipFrom": {
            "name": "Warehouse",
            "addressLine1": "456 Warehouse Ave",
            "city": "Los Angeles",
            "stateOrRegion": "CA",
            "postalCode": "90001",
            "countryCode": "US"
        },
        "containers": [
            {
                "containerType": "PACKAGE",
                "weight": {"unit": "g", "value": 1000},
                "dimensions": {
                    "length": 30, "width": 20, "height": 15, "unit": "IN"
                }
            }
        ]
    },
    params={"method": "POST"}  # Explicit POST
)

print(response.payload)  # Access response data
```

## Related Documentation Files

Created in this repo:
- `SP_API_SHIPPING_ANALYSIS.md` - Detailed analysis
- `SP_API_CODE_REFERENCE.py` - Code examples
- `SP_API_SOURCE_SNIPPETS.txt` - Actual source code
- `SP_API_QUICK_SUMMARY.md` - This file

