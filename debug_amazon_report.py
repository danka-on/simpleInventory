"""
Debug script to see what columns are in the Amazon inventory report
"""

from amazon_manager import AmazonManager
from sp_api.api import Reports
import time

manager = AmazonManager()

print("🔄 Requesting Amazon inventory report...\n")

reports_api = Reports(credentials=manager.credentials, marketplace=manager.marketplace)

# Request report
response = reports_api.create_report(reportType='GET_MERCHANT_LISTINGS_ALL_DATA')
report_id = response.payload.get('reportId')

print(f"Report ID: {report_id}")
print("Waiting for report...")

# Wait for completion
for attempt in range(60):
    time.sleep(5)
    status_response = reports_api.get_report(report_id)
    status = status_response.payload.get('processingStatus')
    
    if status == 'DONE':
        print("Report ready!\n")
        
        # Download report
        document_id = status_response.payload.get('reportDocumentId')
        doc_response = reports_api.get_report_document(document_id, download=True)
        
        report_content = doc_response.payload
        
        # Handle dict format
        if isinstance(report_content, dict):
            if 'document' in report_content:
                report_content = report_content['document']
        
        # Convert to string
        if isinstance(report_content, bytes):
            report_content = report_content.decode('utf-8')
        
        # Get first few lines
        lines = report_content.split('\n')[:5]
        
        print("First few lines of report:")
        print("="*80)
        for i, line in enumerate(lines):
            if i == 0:
                # Header line - show columns
                columns = line.split('\t')
                print(f"COLUMNS ({len(columns)} total):")
                for j, col in enumerate(columns, 1):
                    print(f"  {j}. {col}")
            else:
                print(f"\nRow {i}:")
                print(line[:200] + "..." if len(line) > 200 else line)
        
        break
    
    if attempt % 6 == 0:
        print(f"Still waiting... ({status})")
