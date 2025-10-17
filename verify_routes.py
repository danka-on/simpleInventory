#!/usr/bin/env python
"""
Quick verification script for app.py changes
Usage: python verify_routes.py [optional_route_pattern]
"""
import sys

def verify_routes(pattern=None):
    print("=" * 60)
    print("🔍 VERIFYING APP.PY")
    print("=" * 60)
    
    # 1. Check file line count
    try:
        with open('app.py', 'r', encoding='utf-8') as f:
            lines = f.readlines()
            line_count = len(lines)
        print(f"✓ File readable: {line_count} lines")
    except Exception as e:
        print(f"✗ Cannot read file: {e}")
        return False
    
    # 2. Check if pattern exists in file (if provided)
    if pattern:
        matches = [i+1 for i, line in enumerate(lines) if pattern in line]
        if matches:
            print(f"✓ Pattern '{pattern}' found at lines: {matches}")
        else:
            print(f"✗ Pattern '{pattern}' NOT FOUND in file")
    
    # 3. Try importing the app
    print("\n" + "-" * 60)
    print("Attempting to import Flask app...")
    print("-" * 60)
    try:
        from app import app
        print("✓ Flask app imported successfully")
    except Exception as e:
        print(f"✗ Import failed: {e}")
        return False
    
    # 4. List all routes
    print("\n" + "-" * 60)
    print("📋 ALL REGISTERED ROUTES:")
    print("-" * 60)
    routes = sorted([str(rule) for rule in app.url_map.iter_rules()])
    for route in routes:
        # Highlight routes matching pattern
        if pattern and pattern in route:
            print(f"  → {route}  ⭐")
        else:
            print(f"  {route}")
    
    # 5. Check if pattern is in registered routes
    if pattern:
        matching_routes = [r for r in routes if pattern in r]
        print("\n" + "-" * 60)
        if matching_routes:
            print(f"✓ Routes containing '{pattern}':")
            for r in matching_routes:
                print(f"  ✓ {r}")
        else:
            print(f"✗ NO routes found containing '{pattern}'")
            print("\n⚠️  WARNING: Pattern found in file but not registered!")
            print("   → Flask server may need restart")
            print("   → Check for Python syntax errors")
    
    print("\n" + "=" * 60)
    print("✅ VERIFICATION COMPLETE")
    print("=" * 60)
    return True

if __name__ == "__main__":
    pattern = sys.argv[1] if len(sys.argv) > 1 else None
    
    if pattern:
        print(f"Searching for pattern: '{pattern}'\n")
    
    success = verify_routes(pattern)
    sys.exit(0 if success else 1)
