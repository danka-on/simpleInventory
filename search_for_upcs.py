"""
Search for UPC codes in Amazon listing fields
Check all fields for 12-13 digit numeric patterns
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from sp_api.api import Reports
from amazon_manager import AmazonManager
import time
import re

def search_for_upcs():
    """Search all Amazon listing fields for UPC patterns"""
    
    print("Fetching Amazon inventory report...")
    manager = AmazonManager()
    reports_api = Reports(credentials=manager.credentials, marketplace=manager.marketplace)
    
    try:
        # Request report
        print("🔄 Requesting report...")
        report_id = reports_api.create_report(reportType="GET_MERCHANT_LISTINGS_ALL_DATA")
        report_id = report_id.payload['reportId']
        print(f"📄 Report requested: {report_id}")
        
        # Poll for completion
        print("⏳ Waiting for report to be generated...")
        for _ in range(30):
            time.sleep(5)
            status = reports_api.get_report(report_id)
            processing_status = status.payload.get('processingStatus')
            if processing_status == 'DONE':
                print("✅ Report ready!")
                break
            if _ % 6 == 0:
                print(f"⏳ Still waiting... ({processing_status})")
        
        # Get report document
        document_id = status.payload['reportDocumentId']
        report_data = reports_api.get_report_document(document_id, download=True)
        report_content = report_data.payload
        
        # Handle different formats
        if isinstance(report_content, dict) and 'document' in report_content:
            report_content = report_content['document']
        if isinstance(report_content, bytes):
            report_content = report_content.decode('utf-8')
        
        # Parse TSV
        lines = report_content.strip().split('\n')
        headers = lines[0].split('\t')
        
        print(f"\n✅ Retrieved {len(lines)-1} listings")
        
        # UPC pattern: 12-13 consecutive digits
        upc_pattern = re.compile(r'\b\d{12,13}\b')
        
        # Track UPCs found in each field
        upc_findings = {header: [] for header in headers}
        total_items_checked = 0
        total_upcs_found = 0
        
        # Check each item
        for line in lines[1:]:  # Skip header
            fields = line.split('\t')
            total_items_checked += 1
            
            for i, (header, value) in enumerate(zip(headers, fields)):
                if value.strip():
                    matches = upc_pattern.findall(value)
                    if matches:
                        for match in matches:
                            upc_findings[header].append({
                                'upc': match,
                                'sku': fields[3] if len(fields) > 3 else 'N/A',  # seller-sku column
                                'title': fields[0][:50] if len(fields) > 0 else 'N/A',  # item-name
                                'full_value': value[:200]
                            })
                            total_upcs_found += 1
        
        # Display results
        print("\n" + "=" * 80)
        print("UPC SEARCH RESULTS:")
        print("=" * 80)
        print(f"Total items checked: {total_items_checked}")
        print(f"Total UPC patterns found: {total_upcs_found}")
        
        # Show findings by field
        for header in headers:
            findings = upc_findings[header]
            if findings:
                print(f"\n{header.upper()} ({len(findings)} UPCs found):")
                print("-" * 80)
                
                # Show first 5 examples
                for i, finding in enumerate(findings[:5], 1):
                    print(f"\nExample {i}:")
                    print(f"  UPC: {finding['upc']}")
                    print(f"  SKU: {finding['sku']}")
                    print(f"  Title: {finding['title']}")
                    print(f"  Full Value: {finding['full_value']}")
                
                if len(findings) > 5:
                    print(f"\n  ... and {len(findings) - 5} more")
        
        # Summary
        print("\n" + "=" * 80)
        print("SUMMARY:")
        print("=" * 80)
        
        fields_with_upcs = {k: len(v) for k, v in upc_findings.items() if v}
        
        if fields_with_upcs:
            print("Fields containing UPCs:")
            for field, count in sorted(fields_with_upcs.items(), key=lambda x: -x[1]):
                print(f"  - {field}: {count} UPCs")
        else:
            print("❌ NO UPC CODES FOUND in any field!")
            print("\nThis means your Amazon listings likely use ASINs as product IDs.")
            print("product-id-type=1 indicates ASIN is used instead of UPC/EAN.")
            print("\nTo get actual UPCs, you would need to:")
            print("  1. Query the Catalog Items API using the ASIN")
            print("  2. The catalog data includes UPC/EAN if Amazon has it on file")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    search_for_upcs()
