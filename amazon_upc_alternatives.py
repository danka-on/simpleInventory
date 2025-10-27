"""
Alternative UPC lookup using external services
Since Amazon Catalog API is not accessible, we'll try:
1. Searching by product title on UPC databases
2. Manual CSV import option
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import sqlite3
import time
import requests

def search_upc_by_title(title):
    """Search for UPC using free UPC database"""
    try:
        # Option 1: Try UPCitemdb.com API (requires free API key)
        # You can get a free key at: https://www.upcitemdb.com/api
        
        # For now, we'll show what's available
        print(f"  Searching: {title[:50]}")
        
        # This would work with an API key:
        # url = "https://api.upcitemdb.com/prod/trial/search"
        # params = {"s": title}
        # headers = {"user_key": "YOUR_API_KEY"}
        # response = requests.get(url, params=params, headers=headers)
        
        return None  # Placeholder
        
    except Exception as e:
        print(f"  Error: {e}")
        return None

def export_for_manual_upc_entry():
    """Export Amazon items to CSV for manual UPC entry"""
    
    print("\nExporting Amazon items for manual UPC entry...")
    print("=" * 80)
    
    conn = sqlite3.connect('amazonStore.db')
    cur = conn.cursor()
    
    cur.execute('''
        SELECT ASIN, SKU, TITLE, UPC, PRICE, QUANTITY 
        FROM ITEMS 
        ORDER BY TITLE
    ''')
    
    items = cur.fetchall()
    conn.close()
    
    # Create CSV file
    import csv
    filename = 'amazon_items_for_upc_entry.csv'
    
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['ASIN', 'SKU', 'TITLE', 'CURRENT_UPC', 'NEW_UPC', 'PRICE', 'QTY'])
        
        for item in items:
            asin, sku, title, upc, price, qty = item
            writer.writerow([asin, sku, title, upc or '', '', price, qty])
    
    print(f"✅ Exported {len(items)} items to: {filename}")
    print("\nInstructions:")
    print("1. Open the CSV file in Excel/Google Sheets")
    print("2. Fill in the 'NEW_UPC' column with actual UPC codes")
    print("3. Save the file")
    print("4. Run the import script to update the database")
    
    return filename

def import_upcs_from_csv(filename):
    """Import UPCs from manually edited CSV"""
    
    print(f"\nImporting UPCs from {filename}...")
    print("=" * 80)
    
    import csv
    
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            updates = []
            
            for row in reader:
                new_upc = row.get('NEW_UPC', '').strip()
                if new_upc and new_upc != row.get('CURRENT_UPC', ''):
                    asin = row['ASIN']
                    updates.append((new_upc, asin))
        
        if updates:
            conn = sqlite3.connect('amazonStore.db')
            cur = conn.cursor()
            
            for upc, asin in updates:
                cur.execute('UPDATE ITEMS SET UPC = ? WHERE ASIN = ?', (upc, asin))
            
            conn.commit()
            conn.close()
            
            print(f"✅ Updated {len(updates)} items with UPC codes")
        else:
            print("ℹ️ No new UPC codes found in CSV")
            
    except FileNotFoundError:
        print(f"❌ File not found: {filename}")
    except Exception as e:
        print(f"❌ Error: {e}")

def show_sample_items():
    """Show sample items that need UPCs"""
    
    print("\nSample Amazon items (first 20):")
    print("=" * 80)
    
    conn = sqlite3.connect('amazonStore.db')
    cur = conn.cursor()
    
    cur.execute('''
        SELECT ASIN, SKU, TITLE, UPC 
        FROM ITEMS 
        LIMIT 20
    ''')
    
    items = cur.fetchall()
    conn.close()
    
    for i, (asin, sku, title, upc) in enumerate(items, 1):
        upc_display = upc if upc and upc != asin else "(no UPC)"
        print(f"{i:2}. {asin} | {sku[:15]:15} | {title[:40]:40} | {upc_display}")

def main():
    print("Amazon UPC Lookup Alternatives")
    print("=" * 80)
    print("\nSince Amazon Catalog API is not accessible, here are your options:\n")
    
    print("Option 1: Manual CSV Entry (RECOMMENDED)")
    print("-" * 80)
    print("Export items to CSV, manually add UPCs, then import back")
    print("\nCommands:")
    print("  python this_script.py export   # Creates CSV file")
    print("  python this_script.py import   # Imports UPCs from edited CSV")
    
    print("\n\nOption 2: External UPC API Services")
    print("-" * 80)
    print("Use third-party UPC databases:")
    print("  • UPCitemdb.com (free tier: 100 requests/day)")
    print("  • Barcodelookup.com (free tier: 100 requests/day)")
    print("  • UPC Database (upcdatabase.org)")
    
    print("\n\nOption 3: Keep ASINs")
    print("-" * 80)
    print("ASINs work fine as product identifiers for Amazon-specific operations")
    
    print("\n" + "=" * 80)
    show_sample_items()
    
    print("\n" + "=" * 80)
    print("Ready to export items for manual UPC entry? (y/n): ", end='')
    
    import sys
    if len(sys.argv) > 1:
        if sys.argv[1] == 'export':
            export_for_manual_upc_entry()
        elif sys.argv[1] == 'import':
            import_upcs_from_csv('amazon_items_for_upc_entry.csv')
    else:
        # Interactive mode
        response = input()
        if response.lower() == 'y':
            export_for_manual_upc_entry()

if __name__ == "__main__":
    main()
