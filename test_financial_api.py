"""
Test the financial analytics API to verify BOL data
"""
import requests

print("Testing Financial Analytics API with BOL data...\n")

try:
    response = requests.get('http://localhost:5000/api/financial-analytics')
    data = response.json()
    
    if data['success']:
        print(f"✅ API Success!")
        print(f"Total transactions: {data['count']}")
        print(f"\nBOL Stats:")
        for bol_num, stats in data.get('bol_stats', {}).items():
            print(f"  BOL {bol_num}: {stats['item_count']} items, Total Cost: ${stats['total_cost']:.2f}")
        
        print(f"\nSample transactions with BOL:")
        with_bol = [t for t in data['transactions'] if t.get('bol_number')]
        print(f"Found {len(with_bol)} transactions with BOL numbers")
        
        for trans in with_bol[:5]:
            print(f"  - {trans['title'][:40]}... | BOL: {trans['bol_number']} | Cost: ${trans.get('cost', 0)}")
    else:
        print(f"❌ API Error: {data.get('error')}")
        
except Exception as e:
    print(f"❌ Failed to test API: {e}")
    print("Make sure Flask app is running on port 5000")
