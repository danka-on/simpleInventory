"""
Quick test script to verify printer dependencies and configuration
"""

import sys

print("=" * 60)
print("PRINTER SETUP VERIFICATION")
print("=" * 60)

# Check if python-barcode is installed
print("\n1. Checking python-barcode...")
try:
    import barcode
    from barcode.writer import ImageWriter
    print("   ✓ python-barcode is installed")
    print(f"   Version: {barcode.__version__ if hasattr(barcode, '__version__') else 'unknown'}")
except ImportError as e:
    print("   ✗ python-barcode NOT installed")
    print("   Install with: pip install python-barcode")

# Check if Pillow is installed (required for barcode images)
print("\n2. Checking Pillow (PIL)...")
try:
    from PIL import Image, ImageDraw, ImageFont
    print("   ✓ Pillow is installed")
    import PIL
    print(f"   Version: {PIL.__version__}")
except ImportError:
    print("   ✗ Pillow NOT installed")
    print("   Install with: pip install Pillow")

# Check if python-escpos is installed
print("\n3. Checking python-escpos (for Bluetooth printing)...")
try:
    from escpos.printer import Bluetooth, Dummy
    from escpos.exceptions import Error as ESCPOSError
    print("   ✓ python-escpos is installed")
except ImportError:
    print("   ✗ python-escpos NOT installed")
    print("   Install with: pip install python-escpos")
    print("   Note: This is required for actual Bluetooth printing")

# Check if pybluez is installed
print("\n4. Checking pybluez (for Bluetooth scanning)...")
try:
    import bluetooth
    print("   ✓ pybluez is installed")
except ImportError:
    print("   ✗ pybluez NOT installed")
    print("   Install with: pip install pybluez")
    print("   Note: This is only needed for scanning devices")
    print("   You can manually enter the Bluetooth MAC address without this")

# Check printer configuration
print("\n5. Checking printer configuration...")
try:
    import sqlite3
    conn = sqlite3.connect('bol.db')
    cur = conn.cursor()
    
    # Check if table exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='printer_config'")
    if cur.fetchone():
        print("   ✓ printer_config table exists")
        
        # Check if configured
        cur.execute('SELECT printer_type, bluetooth_address, printer_name FROM printer_config WHERE id = 1')
        config = cur.fetchone()
        if config:
            print(f"   ✓ Printer configured:")
            print(f"      Type: {config[0]}")
            print(f"      Bluetooth Address: {config[1]}")
            print(f"      Name: {config[2] or 'Not set'}")
        else:
            print("   ⚠ No printer configured yet")
            print("   Go to /printer-settings to configure")
    else:
        print("   ⚠ printer_config table doesn't exist yet")
        print("   It will be created automatically when you save printer settings")
    
    conn.close()
except Exception as e:
    print(f"   ✗ Error checking database: {e}")

# Test barcode generation
print("\n6. Testing barcode generation...")
try:
    from printer_manager import printer_manager
    
    # Try to generate a test barcode
    test_upc = "012345678905"
    img = printer_manager.generate_barcode_image(test_upc, "Test Item")
    print(f"   ✓ Successfully generated barcode for UPC: {test_upc}")
    print(f"   Image size: {img.size[0]}x{img.size[1]} pixels")
    
    # Try to save it
    import os
    os.makedirs('static/barcodes', exist_ok=True)
    filepath = f"static/barcodes/test_{test_upc}.png"
    img.save(filepath)
    print(f"   ✓ Saved test barcode to: {filepath}")
    print(f"   You can view it at: http://localhost:5000/barcodes/test_{test_upc}.png")
    
except Exception as e:
    print(f"   ✗ Error generating barcode: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)

print("""
For basic barcode generation (works without printer):
  - Requires: python-barcode, Pillow
  
For Bluetooth printing:
  - Requires: python-barcode, Pillow, python-escpos
  - Optional: pybluez (for device scanning)
  
To install all dependencies:
  pip install python-barcode Pillow python-escpos pybluez

If pybluez fails on Windows (common), you can skip it and manually
enter the Bluetooth MAC address in printer settings.
""")

print("=" * 60)
