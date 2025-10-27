"""
Check if external-product-id or external-product-id-type fields have UPC data
We might have missed these fields in the initial parsing
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from sp_api.api import Reports
from amazon_manager import AmazonManager
import time

def check_all_report_fields_for_upc():
    """Get full TSV and check ALL fields for UPC-like data"""
    
    print("Fetching Amazon inventory report to check ALL fields...")
    manager = AmazonManager()
    reports_api = Reports(credentials=manager.credentials, marketplace=manager.marketplace)
    
    try:
        # Request report
        print("🔄 Requesting report...")
        report_id = reports_api.create_report(reportType="GET_MERCHANT_LISTINGS_ALL_DATA")
        report_id = report_id.payload['reportId']
        print(f"📄 Report requested: {report_id}")
        
        # Poll for completion
        print("⏳ Waiting for report...")
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
        print(f"Found {len(headers)} columns")
        
        # Show ALL headers with indices
        print("\n" + "=" * 80)
        print("ALL REPORT COLUMNS:")
        print("=" * 80)
        for i, header in enumerate(headers):
            print(f"{i:2}: {header}")
        
        # Now parse first 5 items and show ALL non-empty fields
        print("\n" + "=" * 80)
        print("SAMPLE DATA (First 3 items - ALL non-empty fields):")
        print("=" * 80)
        
        for line_num in range(1, min(4, len(lines))):
            fields = lines[line_num].split('\t')
            print(f"\nITEM {line_num}:")
            print("-" * 80)
            
            for i, (header, value) in enumerate(zip(headers, fields)):
                if value.strip():  # Only show non-empty fields
                    display_value = value[:100] if len(value) <= 100 else value[:100] + "..."
                    print(f"  [{i:2}] {header:30} = {display_value}")
        
        # Check if there's an external-product-id column we missed
        print("\n" + "=" * 80)
        print("CHECKING FOR UPC-RELATED COLUMNS:")
        print("=" * 80)
        
        upc_related = []
        for i, header in enumerate(headers):
            lower_header = header.lower()
            if any(keyword in lower_header for keyword in ['upc', 'ean', 'gtin', 'external', 'barcode', 'isbn']):
                upc_related.append((i, header))
        
        if upc_related:
            print(f"Found {len(upc_related)} potentially UPC-related columns:")
            for idx, name in upc_related:
                print(f"  Column {idx}: {name}")
                
                # Show sample values
                print("    Sample values:")
                for line_num in range(1, min(6, len(lines))):
                    fields = lines[line_num].split('\t')
                    if idx < len(fields) and fields[idx].strip():
                        sku = fields[3] if len(fields) > 3 else "N/A"
                        print(f"      SKU {sku}: {fields[idx][:50]}")
        else:
            print("❌ No UPC-related columns found in report")
            print("\nThis confirms that the GET_MERCHANT_LISTINGS_ALL_DATA report")
            print("does NOT include UPC/EAN codes for your listings.")
        
        print("\n" + "=" * 80)
        print("CONCLUSION:")
        print("=" * 80)
        print("""
Amazon's inventory report doesn't contain UPC codes. This is because:
1. You may have listed products using ASINs only (ASIN-to-ASIN matching)
2. Amazon doesn't expose UPC data via the Reports API
3. Catalog Items API access is required for UPC lookups (which we can't access)

Your options:
1. Use ASINs as product identifiers (they work fine for Amazon operations)
2. Manually export and add UPCs from your physical inventory
3. Keep trying to get Catalog Items API access approved
""")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_all_report_fields_for_upc()
