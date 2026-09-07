"""Integrations for Sweet Shelves."""




try:
    from amazon_manager import AmazonManager
    AMAZON_AVAILABLE = True
    print("✅ Amazon integration loaded successfully")
except ImportError as e:
    AMAZON_AVAILABLE = False
    print(f"⚠️ Warning: Amazon integration not available (import error): {e}")
except Exception as e:
    AMAZON_AVAILABLE = False
    print(f"⚠️ Warning: Amazon integration not available (other error): {e}")


try:
    from BOLextractor import process_bol_excel, process_bol_retail_backfill
    BOL_AVAILABLE = True
except ImportError:
    BOL_AVAILABLE = False
    print("Warning: BOLextractor not available (pandas missing)")
