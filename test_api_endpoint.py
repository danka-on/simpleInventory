import sys
sys.path.insert(0, '.')

from app import app
import traceback

# Make a test request to the financial analytics API
with app.test_request_context():
    try:
        from app import api_financial_analytics
        result = api_financial_analytics()
        print("SUCCESS:", result)
    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
