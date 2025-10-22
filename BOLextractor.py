import pandas as pd
from rawbol_manager import insert_raw_bol_items, log_upload
import io
import os

def process_bol_excel(file, lot_number, import_date):
    """
    Process the uploaded Excel file (.xls or .xlsx), find the row with 'UPC', 
    then read all data below it into rawbol.db.
    Returns: {'success': True, 'inserted': n} or {'success': False, 'error': '...'}
    """
    try:
        file_bytes = file.read()
        print('DEBUG: First 32 bytes of uploaded file:', file_bytes[:32])
        if not file_bytes or len(file_bytes) < 10:
            return {'success': False, 'error': 'Uploaded file is empty or too small.'}
        
        # Check if file is actually HTML (common error when download fails)
        if file_bytes[:6].lower() in (b'<html>', b'<html ', b'<!doct'):
            return {'success': False, 'error': 'File appears to be HTML, not Excel. Please download the actual Excel file.'}
        
        filename = getattr(file, 'filename', None) or getattr(file, 'name', None) or ''
        ext = os.path.splitext(filename)[-1].lower()
        
        if ext not in ('.xls', '.xlsx'):
            return {'success': False, 'error': 'Only .xls and .xlsx files are supported.'}
        
        in_memory_file = io.BytesIO(file_bytes)
        
        # Determine which engine to use
        engine = 'xlrd' if ext == '.xls' else 'openpyxl'
        
        # Read the entire Excel file without header first to find the UPC row
        try:
            df_raw = pd.read_excel(in_memory_file, engine=engine, header=None)
        except Exception as e:
            return {'success': False, 'error': f'Failed to read Excel file: {str(e)}'}
        
        # Find the row containing 'UPC' (case-insensitive)
        upc_row_index = None
        for idx, row in df_raw.iterrows():
            # Check if any cell in the row contains 'UPC'
            if any(str(cell).strip().upper() == 'UPC' for cell in row):
                upc_row_index = idx
                break
        
        if upc_row_index is None:
            return {'success': False, 'error': 'Could not find row with "UPC" header.'}
        
        # Re-read the file using the UPC row as header and skip everything above it
        in_memory_file.seek(0)
        df = pd.read_excel(in_memory_file, engine=engine, header=upc_row_index)
        
        # Clean up column names (strip whitespace)
        df.columns = [str(col).strip() for col in df.columns]
        
        # Validate required columns
        required_columns = ['UPC']
        for col in required_columns:
            if col not in df.columns:
                return {'success': False, 'error': f'Missing required column: {col}'}
        
        # Insert into rawbol.db
        result = insert_raw_bol_items(df, lot_number, import_date)
        
        if result.get('success'):
            # Log the upload
            log_upload(filename, lot_number, import_date, result.get('inserted', 0))
        
        return result
    except Exception as e:
        return {'success': False, 'error': str(e)}