"""
Test grace period settings to ensure fractional hours work correctly.
"""
import requests
import json

BASE_URL = 'http://127.0.0.1:5000'

def test_grace_period():
    print("Testing Grace Period Settings (Fractional Hours Support)")
    print("=" * 60)
    
    # Test 1: Set to 1 minute (0.0167 hours)
    print("\n1. Setting grace period to 1 minute (0.0167 hours)...")
    resp = requests.post(f'{BASE_URL}/api/grace_period', 
                        json={'hours': 0.0167})
    result = resp.json()
    print(f"   Response: {result}")
    assert result['success'], "Failed to set 1 minute"
    assert abs(result['hours'] - 0.0167) < 0.0001, "Hours value incorrect"
    
    # Test 2: Read back the value
    print("\n2. Reading back grace period...")
    resp = requests.get(f'{BASE_URL}/api/grace_period')
    result = resp.json()
    print(f"   Response: {result}")
    assert result['success'], "Failed to read grace period"
    assert abs(result['hours'] - 0.0167) < 0.0001, "Read value doesn't match"
    
    # Test 3: Set to 5 minutes (0.0833 hours)
    print("\n3. Setting grace period to 5 minutes (0.0833 hours)...")
    resp = requests.post(f'{BASE_URL}/api/grace_period', 
                        json={'hours': 0.0833})
    result = resp.json()
    print(f"   Response: {result}")
    assert result['success'], "Failed to set 5 minutes"
    
    # Test 4: Set to normal value (48 hours)
    print("\n4. Setting grace period back to 48 hours...")
    resp = requests.post(f'{BASE_URL}/api/grace_period', 
                        json={'hours': 48})
    result = resp.json()
    print(f"   Response: {result}")
    assert result['success'], "Failed to set 48 hours"
    assert result['hours'] == 48, "Hours value incorrect"
    
    # Test 5: Verify it still works with integer input
    print("\n5. Testing with integer value (24)...")
    resp = requests.post(f'{BASE_URL}/api/grace_period', 
                        json={'hours': 24})
    result = resp.json()
    print(f"   Response: {result}")
    assert result['success'], "Failed to set integer value"
    assert result['hours'] == 24, "Integer value incorrect"
    
    print("\n" + "=" * 60)
    print("✓ All tests passed!")
    print("\nGrace period now supports fractional hours for testing:")
    print("  - 0.0167 hours = 1 minute")
    print("  - 0.0833 hours = 5 minutes")
    print("  - 1 hour = 1 hour")
    print("  - 24-72 hours = standard production values")

if __name__ == '__main__':
    try:
        test_grace_period()
    except requests.exceptions.ConnectionError:
        print("ERROR: Could not connect to server at", BASE_URL)
        print("Please start the app with: python app.py")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
