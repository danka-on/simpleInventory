import requests
import json

print('=== Testing BOL Statistics APIs ===\n')

base_url = 'http://localhost:8080'

# Test 1: rawbol/logs API
print('1. Testing /api/rawbol/logs')
try:
    response = requests.get(f'{base_url}/api/rawbol/logs', timeout=5)
    if response.status_code == 200:
        data = response.json()
        print(f'   ✅ Success: {len(data.get("logs", []))} LOTs returned')
        if data.get('logs'):
            sample = data['logs'][0]
            print(f'   Sample LOT: {sample.get("lot_number")}, Cost: ${sample.get("total_client_cost")}, Items: {sample.get("rows_imported")}')
    else:
        print(f'   ❌ Error: Status {response.status_code}')
except Exception as e:
    print(f'   ❌ Error: {e}')

# Test 2: financial-analytics API
print('\n2. Testing /api/financial-analytics')
try:
    response = requests.get(f'{base_url}/api/financial-analytics', timeout=10)
    if response.status_code == 200:
        data = response.json()
        print(f'   ✅ Success: {data.get("count")} transactions returned')
        
        # Count transactions with cost and bol_number
        transactions = data.get('transactions', [])
        with_cost = sum(1 for t in transactions if t.get('cost'))
        with_bol = sum(1 for t in transactions if t.get('bol_number'))
        
        print(f'   Transactions with cost: {with_cost}/{len(transactions)}')
        print(f'   Transactions with bol_number: {with_bol}/{len(transactions)}')
        
        # Show sample transaction with both
        for t in transactions:
            if t.get('cost') and t.get('bol_number'):
                print(f'   Sample: Order {t.get("order_id")}, UPC {t.get("upc")}, Cost ${t.get("cost"):.2f}, BOL# {t.get("bol_number")}')
                break
    else:
        print(f'   ❌ Error: Status {response.status_code}')
        print(f'   Response: {response.text[:200]}')
except Exception as e:
    print(f'   ❌ Error: {e}')

print('\n3. Check browser console at: http://localhost:8080/bol-statistics')
print('   Press F12 to open developer tools and check for JavaScript errors')
