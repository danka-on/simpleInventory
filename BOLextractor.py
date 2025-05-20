import pandas as pd
import io
import os
from DBmanager import process_bol_excel_db  # Move DB logic to DBmanager

def process_bol_excel(file, import_date):
    """
    Process the uploaded Excel file and import date, extract required columns, check for duplicates by UPC, and insert into SQLite DB (ebayStoreDB).
    Returns: {'success': True} or {'success': False, 'error': '...'}
    """
    try:
        file_bytes = file.read()
        print('DEBUG: First 32 bytes of uploaded file:', file_bytes[:32])
        if not file_bytes or len(file_bytes) < 10:
            return {'success': False, 'error': 'Uploaded file is empty or too small.'}
        in_memory_file = io.BytesIO(file_bytes)
        filename = getattr(file, 'filename', None) or getattr(file, 'name', None) or ''
        ext = os.path.splitext(filename)[-1].lower()
        try:
            if ext == '.xls':
                return {'success': False, 'error': 'Legacy .xls files are not supported. Please convert your file to .xlsx and try again.'}
            elif ext == '.xlsx':
                df = pd.read_excel(in_memory_file, engine='openpyxl')
            else:
                return {'success': False, 'error': 'Unsupported file extension: ' + ext}
        except Exception as e:
            return {'success': False, 'error': f'Failed to read Excel file: {str(e)}. First bytes: {file_bytes[:32]}'}
        required_columns = ['UPC', 'ITEM DESCRIPTION', 'CLIENT COST', 'TOTAL CLIENT COST', 'IMAGE', 'LOT #', 'BOL #']
        for col in required_columns:
            if col not in df.columns:
                return {'success': False, 'error': f'Missing required column: {col}'}
        # Move DB logic to DBmanager
        return process_bol_excel_db(df, import_date)
    except Exception as e:
        return {'success': False, 'error': str(e)}