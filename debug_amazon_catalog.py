"""
Debug: Check the full Amazon Catalog API response for Noritake item
"""
from amazon_manager import AmazonManager
import json

print("🔍 Fetching full catalog data for ASIN: B0B57TTYW1\n")

try:
    amazon = AmazonManager()
    result = amazon.get_catalog_item(asin='B0B57TTYW1')
    
    print("📦 Full API Response:")
    print(json.dumps(result, indent=2))
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
