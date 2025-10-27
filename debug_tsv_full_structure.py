"""
Debug: Show actual TSV column names and sample data for UPC-related fields
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from amazon_manager import AmazonManager

def debug_tsv_columns():
    """Show actual TSV structure and look for UPC data"""
    
    print("Fetching Amazon inventory report...")
    manager = AmazonManager()
    
    try:
        # Request report
        from sp_api.api import Reports
        import time
        
        print("🔄 Requesting report...")
        reports_api = Reports(credentials=manager.credentials, marketplace=manager.marketplace)
        
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
            print(f"⏳ Still waiting... ({processing_status})")
        
        # Get report document
        document_id = status.payload['reportDocumentId']
        report_data = reports_api.get_report_document(document_id, download=True)
        
        print(f"Debug - report_data type: {type(report_data)}")
        
        # Access the payload property
        report_content = report_data.payload
        
        print(f"Debug - report_content type: {type(report_content)}")
        
        # Handle different response formats
        if isinstance(report_content, dict) and 'document' in report_content:
            report_content = report_content['document']
        elif isinstance(report_content, dict) and 'url' in report_content:
            import requests
            response = requests.get(report_content['url'])
            report_content = response.text
        
        # Convert bytes to string if needed
        if isinstance(report_content, bytes):
            report_content = report_content.decode('utf-8')
        
        if not report_content or not isinstance(report_content, str):
            print(f"ERROR: report_content is invalid! Type: {type(report_content)}")
            return
        
        # Parse TSV
        lines = report_content.strip().split('\n')
        headers = lines[0].split('\t')
        
        print(f"\n✅ Retrieved {len(lines)-1} listings")
        print("\n" + "=" * 80)
        print(f"FOUND {len(headers)} COLUMNS IN TSV:")
        print("=" * 80)
        
        # Print all headers with index
        for i, header in enumerate(headers):
            print(f"{i:2}: {header}")
        
        # Look for UPC-related columns
        print("\n" + "=" * 80)
        print("UPC-RELATED COLUMNS:")
        print("=" * 80)
        
        upc_keywords = ['upc', 'product', 'asin', 'listing', 'external', 'description', 'note']
        for i, header in enumerate(headers):
            if any(keyword in header.lower() for keyword in upc_keywords):
                print(f"{i:2}: {header}")
        
        # Show sample data for key columns
        print("\n" + "=" * 80)
        print("SAMPLE DATA (first 3 items):")
        print("=" * 80)
        
        for line_num, line in enumerate(lines[1:4], 1):  # First 3 data lines
            fields = line.split('\t')
            print(f"\nItem {line_num}:")
            print("-" * 80)
            
            # Show all fields with non-empty values
            for i, (header, value) in enumerate(zip(headers, fields)):
                if value.strip():
                    # Truncate long values
                    display_value = value[:100] + '...' if len(value) > 100 else value
                    print(f"  [{i:2}] {header:30} = {display_value}")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_tsv_columns()
