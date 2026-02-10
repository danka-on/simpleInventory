#!/usr/bin/env python3
"""Test the improved _mail_sync_amazon function with error reporting."""

import os
import sys
import json

# Add workspace to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Set up minimal Flask app context for the function
from flask import Flask
app = Flask(__name__)

with app.app_context():
    # Import after Flask context is set up
    from app import _mail_sync_amazon
    
    print("=" * 70)
    print("TESTING IMPROVED _mail_sync_amazon() WITH ERROR REPORTING")
    print("=" * 70)
    
    try:
        result = _mail_sync_amazon(days_back=7, max_orders=10)
        print("\n✓ Function executed successfully:")
        print(json.dumps(result, indent=2))
        
        if 'error' in result:
            print(f"\n⚠ Error detected: {result['error']}")
            print(f"Note: {result.get('note', '')}")
    except Exception as e:
        print(f"\n✗ Function raised exception: {e}")
        import traceback
        traceback.print_exc()
