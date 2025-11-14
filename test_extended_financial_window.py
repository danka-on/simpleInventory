#!/usr/bin/env python3
"""
Test syncing Amazon orders with extended Financial Events window
to capture older shipping label purchases
"""

from amazon_manager import AmazonManager

# Initialize Amazon manager
amazon = AmazonManager()

print("=" * 80)
print("SYNCING AMAZON ORDERS WITH EXTENDED FINANCIAL WINDOW")
print("=" * 80)
print("📦 Orders: 30 days")
print("💰 Financial Events: 90 days")
print("=" * 80)

# Sync orders (30 days) but fetch financial events for 90 days
result = amazon.sync_orders_to_db(days_back=30, financial_days_back=90)

print("=" * 80)
print(f"✅ Sync complete: {result} orders processed")
print("=" * 80)
