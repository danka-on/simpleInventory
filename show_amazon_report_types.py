"""
Show other available Amazon report types
"""

print("📊 Available Amazon Report Types\n")
print("="*80)

report_types = {
    "Inventory Reports": [
        ("GET_MERCHANT_LISTINGS_ALL_DATA", "✅ USING - All active listings", "What we use now"),
        ("GET_MERCHANT_LISTINGS_DATA", "All listings including inactive", "Includes deleted/inactive"),
        ("GET_MERCHANT_LISTINGS_INACTIVE_DATA", "Only inactive listings", "Items you've delisted"),
        ("GET_MERCHANT_LISTINGS_DATA_BACK_COMPAT", "Legacy format", "Older format"),
        ("GET_MERCHANT_LISTINGS_DATA_LITE", "Basic listing info", "Minimal data"),
        ("GET_MERCHANT_LISTINGS_DATA_LITER", "Even more basic", "Very minimal"),
        ("GET_MERCHANT_CANCELLED_LISTINGS_DATA", "Cancelled listings", "Removed items"),
        ("GET_CONVERGED_FLAT_FILE_SOLD_LISTINGS_DATA", "Sold listings report", "Alternative to Orders API"),
        ("GET_FLAT_FILE_OPEN_LISTINGS_DATA", "Open listings", "Currently listed items"),
    ],
    
    "Fulfillment/FBA Reports": [
        ("GET_FBA_INVENTORY_AGED_DATA", "FBA aged inventory", "Items sitting in Amazon warehouse"),
        ("GET_FBA_INVENTORY_PLANNING_DATA", "FBA inventory planning", "Stock levels & forecasts"),
        ("GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA", "FBA active inventory", "Current FBA stock"),
        ("GET_FBA_MYI_ALL_INVENTORY_DATA", "All FBA inventory", "All items in Amazon warehouses"),
        ("GET_RESTOCK_INVENTORY_RECOMMENDATIONS_REPORT", "Restock recommendations", "What to restock"),
        ("GET_FBA_FULFILLMENT_INVENTORY_HEALTH_DATA", "FBA inventory health", "Storage fees, age"),
        ("GET_FBA_INVENTORY_RECEIPTS_DATA", "FBA shipment receipts", "What Amazon received"),
    ],
    
    "Sales & Orders Reports": [
        ("GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL", "All orders by date", "Alternative to Orders API"),
        ("GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_GENERAL", "Orders by update", "Recently updated orders"),
        ("GET_FLAT_FILE_ARCHIVED_ORDERS_DATA_BY_ORDER_DATE", "Archived orders", "Old orders"),
        ("GET_ORDER_REPORT_DATA_INVOICING", "Orders for invoicing", "Tax invoice data"),
        ("GET_ORDER_REPORT_DATA_TAX", "Tax report", "Tax information"),
        ("GET_ORDER_REPORT_DATA_SHIPPING", "Shipping report", "Shipping details"),
    ],
    
    "Performance & Analytics": [
        ("GET_SELLER_FEEDBACK_DATA", "Seller feedback", "Customer reviews of you"),
        ("GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE", "Settlement reports", "Payment details"),
        ("GET_V2_SETTLEMENT_REPORT_DATA_XML", "Settlement (XML)", "Payment XML format"),
        ("GET_BRAND_ANALYTICS_MARKET_BASKET_REPORT", "Market basket", "What customers buy together"),
        ("GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT", "Search terms", "What customers search"),
        ("GET_BRAND_ANALYTICS_REPEAT_PURCHASE_REPORT", "Repeat purchase", "Repeat customer data"),
    ],
    
    "Returns & Replacements": [
        ("GET_FLAT_FILE_RETURNS_DATA_BY_RETURN_DATE", "Returns by date", "Customer returns"),
        ("GET_XML_RETURNS_DATA_BY_RETURN_DATE", "Returns XML", "Returns in XML"),
        ("GET_FLAT_FILE_ACTIONABLE_ORDER_DATA_SHIPPING", "Actionable orders", "Orders needing action"),
    ]
}

for category, reports in report_types.items():
    print(f"\n{category}:")
    print("-" * 80)
    for report_type, name, description in reports:
        marker = "✅" if "USING" in name else "  "
        print(f"{marker} {name:50} | {description}")

print("\n\n" + "="*80)
print("💡 Currently Using:")
print("="*80)
print("✅ GET_MERCHANT_LISTINGS_ALL_DATA - For active inventory listings")
print("✅ Orders API - For sold orders (not reports)")

print("\n\n📝 Potentially Useful to Add:")
print("="*80)
print("\n🔹 For Better Inventory Management:")
print("  • GET_MERCHANT_LISTINGS_INACTIVE_DATA - Track delisted items")
print("  • GET_FBA_MYI_ALL_INVENTORY_DATA - If you use FBA fulfillment")
print("  • GET_RESTOCK_INVENTORY_RECOMMENDATIONS_REPORT - Know what to restock")

print("\n🔹 For Sales Analysis:")
print("  • GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE - Payment & fee details")
print("  • GET_SELLER_FEEDBACK_DATA - Customer satisfaction tracking")
print("  • GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT - SEO insights")

print("\n🔹 For Customer Service:")
print("  • GET_FLAT_FILE_RETURNS_DATA_BY_RETURN_DATE - Track returns")
print("  • GET_FLAT_FILE_ACTIONABLE_ORDER_DATA_SHIPPING - Orders needing attention")

print("\n" + "="*80)
