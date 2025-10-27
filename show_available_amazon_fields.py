"""
Show what data is available in Amazon inventory reports
"""

print("📊 Amazon Inventory Report - Available Columns\n")
print("="*80)

all_columns = {
    1: ("item-name", "✅ Captured as TITLE"),
    2: ("item-description", "❌ NOT captured - Product description text"),
    3: ("listing-id", "❌ NOT captured - Amazon's internal listing ID"),
    4: ("seller-sku", "✅ Captured as SKU"),
    5: ("price", "✅ Captured as PRICE"),
    6: ("quantity", "✅ Captured as QUANTITY"),
    7: ("open-date", "❌ NOT captured - When listing was created"),
    8: ("image-url", "✅ Captured as IMAGE"),
    9: ("item-is-marketplace", "❌ NOT captured - Marketplace flag"),
    10: ("product-id-type", "⚠️ Captured but not stored - Type of product ID (UPC/EAN/ISBN)"),
    11: ("zshop-shipping-fee", "❌ NOT captured - Shipping fee amount"),
    12: ("item-note", "❌ NOT captured - Seller notes"),
    13: ("item-condition", "✅ Captured as CONDITION"),
    14: ("zshop-category1", "❌ NOT captured - Amazon category"),
    15: ("zshop-browse-path", "❌ NOT captured - Category browse path"),
    16: ("zshop-storefront-feature", "❌ NOT captured - Storefront feature"),
    17: ("asin1", "✅ Captured as ASIN"),
    18: ("asin2", "❌ NOT captured - Additional ASIN"),
    19: ("asin3", "❌ NOT captured - Additional ASIN"),
    20: ("will-ship-internationally", "❌ NOT captured - International shipping flag"),
    21: ("expedited-shipping", "❌ NOT captured - Expedited shipping flag"),
    22: ("zshop-boldface", "❌ NOT captured - Bold listing feature"),
    23: ("product-id", "✅ Captured as UPC"),
    24: ("bid-for-featured-placement", "❌ NOT captured - Featured placement bid"),
    25: ("add-delete", "❌ NOT captured - Add/delete flag"),
    26: ("pending-quantity", "❌ NOT captured - Quantity pending fulfillment"),
    27: ("fulfillment-channel", "✅ Captured as FULFILLMENT_CHANNEL"),
    28: ("merchant-shipping-group", "❌ NOT captured - Shipping group"),
    29: ("status", "✅ Captured as STATUS")
}

print("\nCurrently Capturing (9 fields):")
print("-" * 80)
for num, (col_name, desc) in all_columns.items():
    if "✅" in desc:
        captured_as = desc.replace("✅ Captured as ", "")
        print(f"  ✅ {col_name:30} → {captured_as}")

print("\n\nNot Currently Capturing (19 fields):")
print("-" * 80)
for num, (col_name, desc) in all_columns.items():
    if "❌" in desc:
        info = desc.replace("❌ NOT captured - ", "")
        print(f"  {num:2}. {col_name:30} - {info}")

print("\n\n📝 Potentially Useful Fields to Add:\n")
print("High Priority:")
print("  • item-description      - Full product description")
print("  • open-date            - When you listed the item")
print("  • pending-quantity     - Items pending shipment")
print("  • listing-id           - Amazon's internal ID")
print("  • zshop-category1      - Product category")

print("\nMedium Priority:")
print("  • item-note            - Your seller notes")
print("  • will-ship-internationally - Shipping settings")
print("  • expedited-shipping   - Shipping options")
print("  • merchant-shipping-group - Shipping template")

print("\nLow Priority:")
print("  • asin2, asin3         - Additional ASINs (usually same as asin1)")
print("  • product-id-type      - Type identifier (UPC vs EAN vs ISBN)")
print("  • Various zshop fields - Legacy fields, mostly unused")

print("\n" + "="*80)
print("💡 TIP: We can add any of these fields to the database schema")
print("="*80)
