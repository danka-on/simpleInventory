# Quick Reference: SP-API Shipping Class Internal Mechanisms
# Source: python-amazon-sp-api v1.9.50

# ============================================================================
# 1. BASE CLASS & REQUEST METHOD
# ============================================================================

# Shipping inherits from: sp_api.base.client.Client
# All HTTP requests go through: Client._request(path, **kwargs)

# Location: sp_api/base/client.py, lines 83-127
# The _request() method signature:
# def _request(self, path, *, data=None, params=None, headers=None,
#              add_marketplace=True, res_no_data=False, bulk=False,
#              wrap_list=False) -> ApiResponse

# What _request() does:
# 1. Extracts HTTP method: params.pop("method", "GET")
# 2. Adds marketplace ID automatically
# 3. Uses requests library to make HTTP call
# 4. Adds OAuth token in x-amz-access-token header
# 5. Parses JSON response and handles errors


# ============================================================================
# 2. HTTP REQUEST CODE
# ============================================================================

# From Client._request() in sp_api/base/client.py:
# from requests import request
# 
# res = request(
#     self.method,                                    # GET, POST, etc.
#     self.endpoint + self._check_version(path),     # Full URL
#     params=params,                                  # Query params
#     data=json.dumps(data) if ... else None,         # JSON body
#     headers=headers or self.headers,                # Auth headers
#     timeout=self.timeout,
#     proxies=self.proxies,
#     verify=self.verify,
# )


# ============================================================================
# 3. AUTHENTICATION HEADERS
# ============================================================================

# Automatically added by Client.headers property:
# {
#     "host": "sellingpartnerapi-na.amazon.com",
#     "user-agent": "python-sp-api-1.9.50",
#     "x-amz-access-token": "<OAuth access token>",
#     "x-amz-date": "20250209T120000Z",
#     "content-type": "application/json",
# }


# ============================================================================
# 4. BASE URLS
# ============================================================================

# Production URLs by region:
# North America: https://sellingpartnerapi-na.amazon.com (US, CA, MX, BR)
# Europe: https://sellingpartnerapi-eu.amazon.com (DE, FR, GB, IT, ES, etc.)
# Far East: https://sellingpartnerapi-fe.amazon.com (JP, SG, AU)

# Sandbox: https://sandbox.sellingpartnerapi-{region}.amazon.com

# Shipping endpoints all start with: /shipping/v1/


# ============================================================================
# 5. CUSTOM REQUESTS - RECOMMENDED METHOD
# ============================================================================

from sp_api.api.shipping import Shipping
from sp_api.base import Marketplaces

shipping = Shipping(
    marketplace=Marketplaces.US,
    refresh_token="your_token"
)

# Direct call to _request() for custom endpoints:
response = shipping._request(
    "/shipping/v1/account",
    params={"method": "GET"}
)

# For POST:
response = shipping._request(
    "/shipping/v1/shipments",
    data={"clientReferenceId": "test", "shipTo": {...}, ...},
    params={"method": "POST"}
)

# Benefits:
# - Auto OAuth token handling
# - Auto marketplace ID addition
# - Auto error handling
# - Auto response parsing


# ============================================================================
# 6. GRANTLESS OPERATIONS
# ============================================================================

response = shipping._request_grantless_operation(
    path="/shipping/v1/endpoint",
    data={"key": "value"},
    params={"method": "POST"}
)


# ============================================================================
# 7. RAW ACCESS (NOT RECOMMENDED)
# ============================================================================

# Get access token directly:
access_token = shipping.auth.access_token

# Make manual requests:
import requests
response = requests.post(
    f"{shipping.endpoint}/shipping/v1/shipments",
    json={"data": "..."},
    headers={
        "x-amz-access-token": access_token,
        "x-amz-date": "...",
        "content-type": "application/json",
    }
)


# ============================================================================
# 8. SHIPPING ENDPOINTS
# ============================================================================

# POST   /shipping/v1/shipments
# GET    /shipping/v1/shipments/{shipmentId}
# POST   /shipping/v1/shipments/{shipmentId}/cancel
# POST   /shipping/v1/shipments/{shipmentId}/purchaseLabels
# POST   /shipping/v1/shipments/{shipmentId}/label
# POST   /shipping/v1/purchaseShipment
# POST   /shipping/v1/rates
# GET    /shipping/v1/account
# GET    /shipping/v1/tracking/{trackingId}


# ============================================================================
# 9. KEY POINTS
# ============================================================================

# - HTTP Library: requests (standard Python)
# - Auth: OAuth 2.0 token in x-amz-access-token header
# - Token Refresh: Automatic via Client._auth.get_auth()
# - Marketplace: Auto-added to requests (unless disabled)
# - Error Handling: Exceptions raised via get_exception_for_code()
# - Response: ApiResponse object with payload, headers, errors

