import pandas as pd
from rawbol_manager import insert_raw_bol_items, log_upload
import io
import os
from io import StringIO

def process_bol_excel(file, lot_number, import_date):
    """
    Process the uploaded Excel file (.xls, .xlsx, or .csv), find the row with 'UPC', 
    then read all data below it into rawbol.db.
    Returns: {'success': True, 'inserted': n} or {'success': False, 'error': '...'}
    """
    try:
        file_bytes = file.read()
        print('DEBUG: First 32 bytes of uploaded file:', file_bytes[:32])
        if not file_bytes or len(file_bytes) < 10:
            return {'success': False, 'error': 'Uploaded file is empty or too small.'}
        
        # Get filename early for debug purposes
        filename = getattr(file, 'filename', None) or getattr(file, 'name', None) or ''
        
        # Check if file is actually HTML (common error when download fails)
        # Look for HTML indicators in the first 32 bytes
        first_32 = file_bytes[:32].lower()
        html_indicators = [b'<html>', b'<html ', b'<!doct', b'<head>', b'<body>']
        is_html = any(indicator in first_32 for indicator in html_indicators)
        
        # Flag to track if we successfully parsed HTML
        html_parsed = False
        
        if is_html:
            # Save the uploaded bytes to a debug file for inspection
            try:
                debug_dir = os.path.join(os.getcwd(), 'debug_uploads')
                os.makedirs(debug_dir, exist_ok=True)
                safe_name = (os.path.basename(filename) or 'upload')
                timestamp = str(int(__import__('time').time()))
                debug_path = os.path.join(debug_dir, f"{safe_name}.{timestamp}.debug")
                with open(debug_path, 'wb') as dbg:
                    dbg.write(file_bytes)
            except Exception as save_e:
                debug_path = None
                print(f'WARNING: Failed to save debug upload: {save_e}')

            # Try to parse HTML table as fallback
            try:
                print("DEBUG: Attempting to parse HTML as table...")
                html_content = file_bytes.decode('utf-8', errors='replace')
                tables = pd.read_html(StringIO(html_content))
                
                if not tables:
                    raise ValueError("No tables found in HTML")
                
                # Find the table with UPC data
                df_raw = None
                for table in tables:
                    # Look for a table that has 'UPC' in its columns or data
                    table_str = table.to_string().upper()
                    if 'UPC' in table_str:
                        df_raw = table
                        break
                
                if df_raw is None:
                    # If no table has UPC in header, try the first table with reasonable data
                    for table in tables:
                        if len(table.columns) > 3 and len(table) > 1:  # At least 4 columns and 2 rows
                            df_raw = table
                            break
                
                # If pandas parsing didn't work well, try BeautifulSoup
                if df_raw is None or not any('UPC' in str(col).upper() for col in df_raw.columns):
                    print("DEBUG: Pandas HTML parsing unsuccessful, trying BeautifulSoup...")
                    from bs4 import BeautifulSoup
                    
                    soup = BeautifulSoup(html_content, 'html.parser')
                    table = soup.find('table')
                    
                    if table:
                        rows = table.find_all('tr')
                        if len(rows) > 1:  # Need at least header + 1 data row
                            # Extract data from table rows
                            table_data = []
                            max_cols = 0
                            for row in rows:
                                cells = row.find_all(['td', 'th'])
                                if cells:  # Skip empty rows
                                    row_data = [cell.get_text(strip=True) for cell in cells]
                                    if row_data and any(cell for cell in row_data):  # Skip completely empty rows
                                        table_data.append(row_data)
                                        max_cols = max(max_cols, len(row_data))
                            
                            if len(table_data) > 1:
                                # Pad shorter rows with empty strings to match max_cols
                                for row in table_data:
                                    while len(row) < max_cols:
                                        row.append('')
                                
                                # Convert to DataFrame
                                df_raw = pd.DataFrame(table_data[1:], columns=table_data[0] if table_data else None)
                                print(f"DEBUG: BeautifulSoup extracted {len(df_raw)} rows with {len(df_raw.columns)} columns")
                
                # For HTML tables, pandas.read_html() or BeautifulSoup should have already parsed headers correctly
                # Just clean up the DataFrame
                df = df_raw.copy()
                
                # Clean up column names (strip whitespace)
                df.columns = [str(col).strip() for col in df.columns]
                
                # Clean currency-formatted columns (remove $ and convert to float)
                currency_columns = ['CLIENT COST', 'TOTAL CLIENT COST', 'ORIGINAL COST', 'TOTAL ORIGINAL COST', 'ORIGINAL RETAIL', 'TOTAL ORIGINAL RETAIL']
                for col in currency_columns:
                    if col in df.columns:
                        df[col] = df[col].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False).str.strip()
                        # Convert to numeric, keeping NaN for empty strings
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                
                print(f"DEBUG: Processed HTML table into DataFrame with columns: {list(df.columns)}")
                html_parsed = True
                
            except Exception as html_e:
                # If HTML parsing fails, provide the original error
                preview = first_32
                try:
                    preview_text = preview.decode('utf-8', errors='replace')
                except Exception:
                    preview_text = str(preview)
                print(f"DEBUG: HTML parsing failed: {html_e}")
                err_msg = 'File appears to be HTML, not Excel. Please download the actual Excel file.'
                if debug_path:
                    err_msg += f' Debug file saved at: {debug_path}'
                err_detail = f'Preview (first 32 bytes): {preview_text}'
                return {'success': False, 'error': err_msg, 'detail': err_detail}
                # If HTML parsing fails, provide the original error
                preview = first_32
                try:
                    preview_text = preview.decode('utf-8', errors='replace')
                except Exception:
                    preview_text = str(preview)
                print(f"DEBUG: HTML parsing failed: {html_e}")
                err_msg = 'File appears to be HTML, not Excel. Please download the actual Excel file.'
                if debug_path:
                    err_msg += f' Debug file saved at: {debug_path}'
                err_detail = f'Preview (first 32 bytes): {preview_text}'
                return {'success': False, 'error': err_msg, 'detail': err_detail}
        
        # Skip Excel processing if HTML was successfully parsed
        if not html_parsed:
            # Check for proper Excel file signatures
            ext = os.path.splitext(filename)[-1].lower()
            
            if ext == '.xls':
                # .xls files (Excel 97-2003) should start with OLE2 signature: D0 CF 11 E0 A1 B1 1A E1
                ole2_signature = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
                # Some .xls files might have slight variations or be BIFF5 format
                biff5_signature = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'  # Same as OLE2 for BIFF8
                if not (file_bytes.startswith(ole2_signature) or file_bytes.startswith(biff5_signature)):
                    # Print debug info for troubleshooting
                    actual_sig = file_bytes[:16].hex()
                    print(f'DEBUG: File signature for {filename}: {actual_sig}')
                    # For .xls files, try to read anyway since signatures can vary
                    print(f'WARNING: {filename} has non-standard signature, attempting to read anyway...')
            elif ext == '.xlsx':
                # .xlsx files should start with ZIP/PK signature: 50 4B 03 04
                zip_signature = b'\x50\x4b\x03\x04'
                if not file_bytes.startswith(zip_signature):
                    return {'success': False, 'error': 'File does not appear to be a valid .xlsx Excel file. Please ensure you are uploading an actual Excel file.'}
            
            if ext not in ('.xls', '.xlsx', '.csv'):
                return {'success': False, 'error': 'Only .xls, .xlsx, and .csv files are supported.'}
            
            in_memory_file = io.BytesIO(file_bytes)
            
            # Determine which engine to use
            if ext == '.csv':
                # For CSV files, read directly
                df_raw = pd.read_csv(in_memory_file, header=None)
            else:
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
            if ext == '.csv':
                df = pd.read_csv(in_memory_file, header=upc_row_index)
            else:
                df = pd.read_excel(in_memory_file, engine=engine, header=upc_row_index)
            
            # Clean up column names (strip whitespace)
            df.columns = [str(col).strip() for col in df.columns]
            
            # Clean currency-formatted columns (remove $ and convert to float)
            currency_columns = ['CLIENT COST', 'TOTAL CLIENT COST', 'ORIGINAL COST', 'TOTAL ORIGINAL COST', 'ORIGINAL RETAIL', 'TOTAL ORIGINAL RETAIL']
            for col in currency_columns:
                if col in df.columns:
                    df[col] = df[col].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False).str.strip()
                    # Convert to numeric, keeping NaN for empty strings
                    df[col] = pd.to_numeric(df[col], errors='coerce')
        
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