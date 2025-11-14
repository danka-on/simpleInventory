#!/usr/bin/env python3
"""
Diagnose why LOT matching has such low success rate.
"""
import sqlite3
from datetime import datetime

def diagnose_matching_issues():
    """Find out why only 1/188 orders matched"""
    
    print("=== LOT Matching Diagnostics ===\n")
    
    # 1. Check date formats in sold.db
    print("1. Checking sold order date formats:")
    sold_conn = sqlite3.connect('sold.db')
    sold_conn.row_factory = sqlite3.Row
    sold_cur = sold_conn.cursor()
    
    sold_cur.execute('''
        SELECT paid_time
        FROM orders
        WHERE paid_time IS NOT NULL
        LIMIT 5
    ''')
    
    for row in sold_cur.fetchall():
        print(f"   Sold date: {row['paid_time']}")
    
    # 2. Check date formats in rawbol.db
    print("\n2. Checking rawbol LOT date formats:")
    rawbol_conn = sqlite3.connect('rawbol.db')
    rawbol_conn.row_factory = sqlite3.Row
    rawbol_cur = rawbol_conn.cursor()
    
    rawbol_cur.execute('''
        SELECT DISTINCT import_date
        FROM raw_bol_items
        WHERE import_date IS NOT NULL
        AND import_date != ''
        ORDER BY import_date DESC
        LIMIT 5
    ''')
    
    for row in rawbol_cur.fetchall():
        print(f"   LOT date: {row['import_date']}")
    
    # 3. Check if UPCs match between sold and rawbol
    print("\n3. Checking UPC matches:")
    sold_cur.execute('''
        SELECT DISTINCT barcode
        FROM orders
        WHERE barcode IS NOT NULL
        AND barcode != ''
        LIMIT 10
    ''')
    
    sold_barcodes = [row['barcode'] for row in sold_cur.fetchall()]
    
    matches_found = 0
    for barcode in sold_barcodes:
        rawbol_cur.execute('''
            SELECT COUNT(*) as count
            FROM raw_bol_items
            WHERE upc = ? COLLATE NOCASE
        ''', (barcode,))
        
        count = rawbol_cur.fetchone()['count']
        if count > 0:
            matches_found += 1
            print(f"   ✓ {barcode}: Found in rawbol ({count} items)")
        else:
            print(f"   ✗ {barcode}: NOT in rawbol")
    
    print(f"\n   Match rate: {matches_found}/{len(sold_barcodes)} = {matches_found/len(sold_barcodes)*100:.1f}%")
    
    # 4. Date comparison test
    print("\n4. Testing date comparison logic:")
    
    # Get one sold order with barcode
    sold_cur.execute('''
        SELECT id, barcode, paid_time
        FROM orders
        WHERE barcode IS NOT NULL
        AND barcode != ''
        AND paid_time IS NOT NULL
        ORDER BY paid_time DESC
        LIMIT 1
    ''')
    
    test_order = sold_cur.fetchone()
    if test_order:
        barcode = test_order['barcode']
        sold_date = test_order['paid_time']
        
        print(f"   Test order:")
        print(f"     UPC: {barcode}")
        print(f"     Sold date: {sold_date}")
        
        # Parse sold date
        try:
            sold_datetime = datetime.fromisoformat(sold_date.replace('Z', '+00:00'))
            print(f"     Parsed sold date: {sold_datetime}")
        except Exception as e:
            print(f"     ERROR parsing sold date: {e}")
            sold_datetime = None
        
        # Find LOTs with this UPC
        rawbol_cur.execute('''
            SELECT lot_number, import_date, quantity
            FROM raw_bol_items
            WHERE upc = ? COLLATE NOCASE
            ORDER BY import_date DESC
        ''', (barcode,))
        
        lots = rawbol_cur.fetchall()
        if lots:
            print(f"     Found {len(lots)} LOT entries for this UPC:")
            for lot in lots:
                import_date = lot['import_date']
                try:
                    lot_datetime = datetime.fromisoformat(import_date)
                    if sold_datetime:
                        is_before = lot_datetime <= sold_datetime
                        status = "✓ BEFORE" if is_before else "✗ AFTER"
                        print(f"       {status} LOT {lot['lot_number']}: {import_date} (qty: {lot['quantity']})")
                    else:
                        print(f"       LOT {lot['lot_number']}: {import_date} (qty: {lot['quantity']})")
                except Exception as e:
                    print(f"       ERROR parsing LOT date '{import_date}': {e}")
        else:
            print(f"     No LOTs found with this UPC")
    
    # 5. Overall statistics
    print("\n5. Overall Statistics:")
    
    sold_cur.execute('''
        SELECT COUNT(*) as total,
               COUNT(DISTINCT barcode) as unique_barcodes
        FROM orders
        WHERE paid_time IS NOT NULL
        AND barcode IS NOT NULL
        AND barcode != ''
    ''')
    sold_stats = sold_cur.fetchone()
    
    rawbol_cur.execute('''
        SELECT COUNT(*) as total_items,
               COUNT(DISTINCT upc) as unique_upcs,
               COUNT(DISTINCT lot_number) as total_lots
        FROM raw_bol_items
    ''')
    rawbol_stats = rawbol_cur.fetchone()
    
    print(f"   Sold orders: {sold_stats['total']} ({sold_stats['unique_barcodes']} unique barcodes)")
    print(f"   Rawbol items: {rawbol_stats['total_items']} ({rawbol_stats['unique_upcs']} unique UPCs, {rawbol_stats['total_lots']} LOTs)")
    
    # 6. Check date range overlap
    print("\n6. Date Range Analysis:")
    
    sold_cur.execute('''
        SELECT MIN(paid_time) as earliest, MAX(paid_time) as latest
        FROM orders
        WHERE paid_time IS NOT NULL
    ''')
    sold_range = sold_cur.fetchone()
    print(f"   Sold orders: {sold_range['earliest']} to {sold_range['latest']}")
    
    rawbol_cur.execute('''
        SELECT MIN(import_date) as earliest, MAX(import_date) as latest
        FROM raw_bol_items
        WHERE import_date IS NOT NULL AND import_date != ''
    ''')
    rawbol_range = rawbol_cur.fetchone()
    print(f"   LOT imports: {rawbol_range['earliest']} to {rawbol_range['latest']}")
    
    sold_conn.close()
    rawbol_conn.close()

if __name__ == '__main__':
    try:
        diagnose_matching_issues()
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
