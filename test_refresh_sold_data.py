"""
Test the refresh sold data API and verify financial analytics improvements
"""
import sqlite3
import requests
import time

print("=== Testing Sold Data Refresh Feature ===\n")

# First, check current state of sold.db
print("1. Checking current state of sold.db...")
sold_conn = sqlite3.connect('sold.db')
sold_conn.row_factory = sqlite3.Row
sold_cur = sold_conn.cursor()

sold_cur.execute('''
    SELECT COUNT(*) as total,
           SUM(CASE WHEN barcode LIKE 'B0%' OR LENGTH(barcode) = 10 THEN 1 ELSE 0 END) as asin_barcodes,
           SUM(CASE WHEN lot_number IS NULL OR lot_number = '' THEN 1 ELSE 0 END) as missing_lot
    FROM orders
    WHERE store = 'amazon' AND barcode IS NOT NULL
''')
stats = sold_cur.fetchone()
print(f"   Total Amazon orders: {stats['total']}")
print(f"   Orders with ASIN-like barcodes: {stats['asin_barcodes']}")
print(f"   Orders missing LOT number: {stats['missing_lot']}")

sold_conn.close()

# Test the financial analytics API (should handle ASINs now)
print("\n2. Testing /api/financial-analytics (should now handle ASIN lookups)...")
try:
    response = requests.get('http://localhost:8080/api/financial-analytics', timeout=10)
    if response.ok:
        data = response.json()
        transactions = data.get('transactions', [])
        
        # Count how many have cost data
        with_cost = sum(1 for t in transactions if t.get('cost'))
        with_lot = sum(1 for t in transactions if t.get('bol_number'))
        
        print(f"   ✅ API returned {len(transactions)} transactions")
        print(f"   📊 {with_cost} have cost data ({with_cost/len(transactions)*100:.1f}%)")
        print(f"   📊 {with_lot} have LOT numbers ({with_lot/len(transactions)*100:.1f}%)")
        
        # Show sample of cost sources
        sources = {}
        for t in transactions:
            if t.get('cost_source'):
                sources[t['cost_source']] = sources.get(t['cost_source'], 0) + 1
        
        if sources:
            print(f"   📊 Cost sources: {sources}")
    else:
        print(f"   ❌ API failed: {response.status_code}")
except Exception as e:
    print(f"   ❌ Error: {e}")

# Test the refresh endpoint
print("\n3. Testing /api/refresh-sold-data endpoint...")
try:
    print("   Calling refresh endpoint...")
    response = requests.post('http://localhost:8080/api/refresh-sold-data', timeout=30)
    if response.ok:
        result = response.json()
        print(f"   ✅ Refresh successful!")
        print(f"   📊 Updated barcodes: {result.get('updated_barcodes', 0)}")
        print(f"   📊 Updated LOT numbers: {result.get('updated_lot_numbers', 0)}")
        print(f"   💬 Message: {result.get('message', 'N/A')}")
    else:
        print(f"   ❌ Refresh failed: {response.status_code}")
        print(f"   Error: {response.text}")
except Exception as e:
    print(f"   ❌ Error: {e}")

# Verify changes in sold.db
print("\n4. Verifying changes in sold.db...")
time.sleep(1)  # Give it a moment
sold_conn = sqlite3.connect('sold.db')
sold_conn.row_factory = sqlite3.Row
sold_cur = sold_conn.cursor()

sold_cur.execute('''
    SELECT COUNT(*) as total,
           SUM(CASE WHEN barcode LIKE 'B0%' OR LENGTH(barcode) = 10 THEN 1 ELSE 0 END) as asin_barcodes,
           SUM(CASE WHEN lot_number IS NULL OR lot_number = '' THEN 1 ELSE 0 END) as missing_lot
    FROM orders
    WHERE store = 'amazon' AND barcode IS NOT NULL
''')
stats_after = sold_cur.fetchone()
print(f"   Total Amazon orders: {stats_after['total']}")
print(f"   Orders with ASIN-like barcodes: {stats_after['asin_barcodes']} (was {stats['asin_barcodes']})")
print(f"   Orders missing LOT number: {stats_after['missing_lot']} (was {stats['missing_lot']})")

if stats_after['asin_barcodes'] < stats['asin_barcodes']:
    print(f"   ✅ Reduced ASIN barcodes by {stats['asin_barcodes'] - stats_after['asin_barcodes']}")

if stats_after['missing_lot'] < stats['missing_lot']:
    print(f"   ✅ Added {stats['missing_lot'] - stats_after['missing_lot']} LOT numbers")

sold_conn.close()

# Test financial analytics again to see improvement
print("\n5. Re-testing /api/financial-analytics after refresh...")
try:
    response = requests.get('http://localhost:8080/api/financial-analytics', timeout=10)
    if response.ok:
        data = response.json()
        transactions = data.get('transactions', [])
        
        with_cost_after = sum(1 for t in transactions if t.get('cost'))
        with_lot_after = sum(1 for t in transactions if t.get('bol_number'))
        
        print(f"   ✅ API returned {len(transactions)} transactions")
        print(f"   📊 {with_cost_after} have cost data ({with_cost_after/len(transactions)*100:.1f}%) - was {with_cost} before")
        print(f"   📊 {with_lot_after} have LOT numbers ({with_lot_after/len(transactions)*100:.1f}%) - was {with_lot} before")
        
        if with_cost_after > with_cost:
            print(f"   ✅ Cost data improved by {with_cost_after - with_cost} items!")
        
        if with_lot_after > with_lot:
            print(f"   ✅ LOT numbers improved by {with_lot_after - with_lot} items!")
    else:
        print(f"   ❌ API failed: {response.status_code}")
except Exception as e:
    print(f"   ❌ Error: {e}")

print("\n=== Test Complete ===")
