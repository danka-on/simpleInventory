import pandas as pd
from rawbol_manager import insert_raw_bol_items, log_upload
import io
import os
import re
import sqlite3
from io import StringIO

def process_bol_excel(file, lot_number, import_date, shipping_cost=None):
    """
    Process the uploaded Excel file (.xls, .xlsx, or .csv), find the row with 'UPC', 
    then read all data below it into rawbol.db.
    Also extracts LOT # and TOTAL CLIENT COST from header section before UPC table.
    Args:
        shipping_cost: Optional shipping cost for this lot (float or None)
    Returns: {'success': True, 'inserted': n, 'extracted_lot_number': str, 'total_client_cost': float} 
             or {'success': False, 'error': '...'}
    """
    try:
        # Initialize header data
        extracted_lot_number = None
        total_client_cost_header = None
        bol_location = None
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
                
                # Find the table with UPC data - prioritize tables with UPC in headers
                df_raw = None
                
                # First priority: table with 'UPC' in column headers
                for table in tables:
                    if any('UPC' in str(col).upper() for col in table.columns):
                        df_raw = table
                        print(f"DEBUG: Found table with UPC in headers: {len(table)} rows, {len(table.columns)} columns")
                        break
                
                # Second priority: table with UPC-like data (long numeric strings)
                if df_raw is None:
                    for table in tables:
                        # Check if any column has UPC-like values (12-13 digit numbers)
                        for col in table.columns:
                            col_values = table[col].dropna().astype(str)
                            upc_candidates = [v for v in col_values if v.isdigit() and 10 <= len(v) <= 14]
                            if len(upc_candidates) > len(col_values) * 0.3:  # At least 30% UPC-like values
                                df_raw = table
                                print(f"DEBUG: Found table with UPC-like data: {len(table)} rows, {len(table.columns)} columns")
                                break
                
                # Third priority: largest table with reasonable data (most columns and rows)
                if df_raw is None:
                    best_table = None
                    best_score = 0
                    for table in tables:
                        # Score based on number of columns and rows
                        score = len(table.columns) * len(table)
                        if score > best_score and len(table.columns) > 3 and len(table) > 1:
                            best_score = score
                            best_table = table
                    if best_table is not None:
                        df_raw = best_table
                        print(f"DEBUG: Using largest table: {len(best_table)} rows, {len(best_table.columns)} columns")
                
                # Special handling for Macy's BOL HTML: single table with multiple sections
                if df_raw is not None and not any('UPC' in str(col).upper() for col in df_raw.columns):
                    print("DEBUG: Table doesn't have UPC in headers, checking for UPC row within table...")
                    # Look for a row containing 'UPC' and use it as header
                    for idx, row in df_raw.iterrows():
                        if any(str(cell).strip().upper() == 'UPC' for cell in row):
                            print(f"DEBUG: Found UPC header in row {idx}, extracting from there")
                            # Create new DataFrame starting from this row as header
                            df_raw = df_raw.iloc[idx:].reset_index(drop=True)
                            # Set the first row as column headers
                            df_raw.columns = df_raw.iloc[0]
                            df_raw = df_raw.iloc[1:].reset_index(drop=True)
                            print(f"DEBUG: Extracted UPC table: {len(df_raw)} rows, {len(df_raw.columns)} columns")
                            break
                
                if df_raw is None:
                    raise ValueError("Could not find a suitable table with item data")
                
                # If pandas parsing didn't work well, try BeautifulSoup
                if df_raw is None or not any('UPC' in str(col).upper() for col in df_raw.columns):
                    print("DEBUG: Pandas HTML parsing unsuccessful, trying BeautifulSoup...")
                    from bs4 import BeautifulSoup
                    
                    soup = BeautifulSoup(html_content, 'html.parser')
                    all_tables = soup.find_all('table')
                    
                    # Find the best table using similar logic
                    best_table = None
                    best_score = 0
                    
                    for table in all_tables:
                        rows = table.find_all('tr')
                        if len(rows) < 2:  # Need at least header + 1 data row
                            continue
                            
                        # Extract header row
                        header_row = rows[0]
                        header_cells = header_row.find_all(['td', 'th'])
                        headers = [cell.get_text(strip=True) for cell in header_cells]
                        
                        # Check for UPC in headers
                        if any('UPC' in header.upper() for header in headers):
                            best_table = table
                            print(f"DEBUG: BeautifulSoup found table with UPC in headers")
                            break
                        
                        # Score based on number of columns and data rows
                        num_cols = len(headers)
                        num_data_rows = len(rows) - 1  # Subtract header row
                        score = num_cols * num_data_rows
                        
                        if score > best_score and num_cols > 3 and num_data_rows > 1:
                            best_score = score
                            best_table = table
                    
                    if best_table:
                        rows = best_table.find_all('tr')
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
                
                print(f"DEBUG: Processed HTML table into DataFrame with columns: {list(df.columns)}")
                
                # Extract header data from HTML content before the table
                print(f"DEBUG: Extracting header data from HTML content...")
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(html_content, 'html.parser')
                    
                    # Try to find structured table data for LOCATION row
                    tables_found = soup.find_all('table')
                    location_row_found = False
                    
                    for table in tables_found:
                        rows = table.find_all('tr')
                        for row_idx, row in enumerate(rows):
                            cells = row.find_all(['td', 'th'])
                            if cells and len(cells) > 0:
                                first_cell = cells[0].get_text(strip=True).upper()
                                if first_cell == 'LOCATION':
                                    print(f"DEBUG: Found LOCATION row in HTML table")
                                    location_row_found = True
                                    
                                    # Find TOTAL CLIENT COST and LOT # column indices
                                    total_cost_col_idx = None
                                    lot_number_col_idx = None
                                    
                                    for col_idx, cell in enumerate(cells):
                                        cell_text = cell.get_text(strip=True).upper()
                                        print(f"DEBUG: HTML Column {col_idx} = '{cell_text}'")
                                        if 'TOTAL' in cell_text and 'CLIENT' in cell_text and 'COST' in cell_text:
                                            total_cost_col_idx = col_idx
                                            print(f"DEBUG: Found TOTAL CLIENT COST at column {col_idx}")
                                        # Look for LOT # column (flexible matching)
                                        if 'LOT' in cell_text and ('#' in cell_text or 'NUMBER' in cell_text or cell_text.endswith('NO') or cell_text.endswith('NO.')):
                                            lot_number_col_idx = col_idx
                                            print(f"DEBUG: Found LOT # column at index {col_idx} in HTML LOCATION row (matched: '{cell_text}')")
                                    
                                    # Extract from first data row after LOCATION header
                                    if row_idx + 1 < len(rows):
                                        first_data_row = rows[row_idx + 1]
                                        first_data_cells = first_data_row.find_all(['td', 'th'])
                                        
                                        # Extract location from first column (LOCATION column)
                                        if len(first_data_cells) > 0 and bol_location is None:
                                            location_value = first_data_cells[0].get_text(strip=True)
                                            if location_value and location_value.lower() != 'nan' and location_value.upper() != 'TOTAL' and location_value.upper() != 'UPC':
                                                bol_location = location_value
                                                print(f"DEBUG: Found BOL location from HTML: '{bol_location}'")
                                        
                                        # Extract LOT # from LOT # column
                                        if lot_number_col_idx is not None and len(first_data_cells) > lot_number_col_idx and extracted_lot_number is None:
                                            lot_value = first_data_cells[lot_number_col_idx].get_text(strip=True)
                                            if lot_value and lot_value.lower() != 'nan' and len(lot_value) < 50:
                                                extracted_lot_number = lot_value
                                                print(f"DEBUG: Found LOT # from HTML LOCATION row: '{extracted_lot_number}'")
                                    
                                    # Go down in subsequent rows to find the last value in that column
                                    if total_cost_col_idx is not None:
                                        for next_row in rows[row_idx + 1:]:
                                            next_cells = next_row.find_all(['td', 'th'])
                                            if len(next_cells) > total_cost_col_idx:
                                                cell_value = next_cells[total_cost_col_idx].get_text(strip=True)
                                                # Check if this is the UPC row (stop before item table)
                                                first_cell_text = next_cells[0].get_text(strip=True).upper()
                                                if first_cell_text == 'UPC':
                                                    break
                                                
                                                if cell_value and cell_value.lower() != 'nan':
                                                    cell_value = cell_value.replace('$', '').replace(',', '').strip()
                                                    try:
                                                        total_client_cost_header = float(cell_value)
                                                        print(f"DEBUG: Found TOTAL CLIENT COST value in HTML: ${total_client_cost_header}")
                                                    except ValueError:
                                                        pass
                                    break
                        if location_row_found:
                            break
                    
                    # Fallback: Get all text content for LOT # extraction
                    all_text = soup.get_text()
                    lines = [line.strip() for line in all_text.split('\n') if line.strip()]
                    
                    # Look for LOT #
                    for i, line in enumerate(lines):
                        line_upper = line.upper().strip()
                        
                        # Look for LOT # or LOT NUMBER (more specific matching)
                        if line_upper in ['LOT #', 'LOT#', 'LOT NUMBER', 'LOT NO', 'LOT NO.', 'LOT'] or line_upper.startswith('LOT #:') or line_upper.startswith('LOT NUMBER:'):
                            # Try to extract the lot number from same line or next line
                            lot_match = line.split(':')[-1].strip() if ':' in line else None
                            if not lot_match or lot_match.upper().startswith('LOT'):
                                # Check next line
                                if i + 1 < len(lines):
                                    lot_match = lines[i + 1].strip()
                            if lot_match and not lot_match.upper().startswith('LOT') and len(lot_match) < 50:
                                extracted_lot_number = lot_match
                                print(f"DEBUG: Found LOT # in HTML: '{extracted_lot_number}'")
                                break
                    
                except Exception as header_e:
                    print(f"DEBUG: Could not extract header data from HTML: {header_e}")
                
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
            
            # Extract header data BEFORE the UPC table
            print(f"DEBUG: Extracting header data from rows 0 to {upc_row_index}...")
            
            # Find the LOCATION row (first header row)
            location_row_index = None
            for idx in range(upc_row_index):
                row = df_raw.iloc[idx]
                first_cell = str(row.iloc[0]).strip().upper()
                if first_cell == 'LOCATION':
                    location_row_index = idx
                    print(f"DEBUG: Found LOCATION header row at index {idx}")
                    break
            
            # Extract TOTAL CLIENT COST, LOT #, and LOCATION from LOCATION row
            if location_row_index is not None:
                location_row = df_raw.iloc[location_row_index]
                total_cost_col_idx = None
                lot_number_col_idx = None
                
                print(f"DEBUG: LOCATION row contents: {list(location_row)}")
                
                # Find TOTAL CLIENT COST and LOT # columns in LOCATION row
                for col_idx, cell in enumerate(location_row):
                    cell_str = str(cell).strip().upper()
                    print(f"DEBUG: Column {col_idx} = '{cell_str}'")
                    if 'TOTAL' in cell_str and 'CLIENT' in cell_str and 'COST' in cell_str:
                        total_cost_col_idx = col_idx
                        print(f"DEBUG: Found TOTAL CLIENT COST column at index {col_idx} in LOCATION row")
                    # Look for LOT # column (flexible matching - contains "LOT" and either "#" or "NUMBER" or "NO")
                    if 'LOT' in cell_str and ('#' in cell_str or 'NUMBER' in cell_str or cell_str.endswith('NO') or cell_str.endswith('NO.')):
                        lot_number_col_idx = col_idx
                        print(f"DEBUG: Found LOT # column at index {col_idx} in LOCATION row (matched: '{cell_str}')")
                
                # Extract from first data row after LOCATION header (row + 1)
                if location_row_index + 1 < upc_row_index:
                    first_data_row = df_raw.iloc[location_row_index + 1]
                    
                    # Extract location from first column (LOCATION column)
                    if bol_location is None:
                        location_value = str(first_data_row.iloc[0]).strip()
                        if location_value and location_value.lower() != 'nan' and location_value.upper() != 'TOTAL':
                            bol_location = location_value
                            print(f"DEBUG: Found BOL location: '{bol_location}'")
                    
                    # Extract LOT # from LOT # column
                    if lot_number_col_idx is not None and extracted_lot_number is None:
                        lot_value = str(first_data_row.iloc[lot_number_col_idx]).strip()
                        if lot_value and lot_value.lower() != 'nan' and len(lot_value) < 50:
                            extracted_lot_number = lot_value
                            print(f"DEBUG: Found LOT # in LOCATION row: '{extracted_lot_number}'")
                
                # Scan down TOTAL CLIENT COST column to find the last non-empty value
                if total_cost_col_idx is not None:
                    for idx in range(location_row_index + 1, upc_row_index):
                        cell_value = df_raw.iloc[idx, total_cost_col_idx]
                        cell_str = str(cell_value).strip()
                        if cell_str and cell_str.lower() != 'nan':
                            # Clean currency formatting
                            cell_str = cell_str.replace('$', '').replace(',', '').strip()
                            try:
                                total_client_cost_header = float(cell_str)
                                print(f"DEBUG: Found TOTAL CLIENT COST value at row {idx}: ${total_client_cost_header}")
                            except ValueError:
                                print(f"DEBUG: Could not convert value '{cell_str}' to float")
            
            # Re-read the file using the UPC row as header and skip everything above it
            in_memory_file.seek(0)
            if ext == '.csv':
                df = pd.read_csv(in_memory_file, header=upc_row_index)
            else:
                df = pd.read_excel(in_memory_file, engine=engine, header=upc_row_index)
            
            # Clean up column names (strip whitespace)
            df.columns = [str(col).strip() for col in df.columns]
        
        # Validate required columns
        required_columns = ['UPC']
        for col in required_columns:
            if col not in df.columns:
                return {'success': False, 'error': f'Missing required column: {col}'}
        
        # Calculate average cost: total BOL cost / total BOL quantity
        avg_cost = None
        if total_client_cost_header:
            total_qty = 0
            for _, row in df.iterrows():
                qty = 1
                if 'QUANTITY' in row:
                    try:
                        qty = int(row['QUANTITY'])
                    except:
                        qty = 1
                elif 'QTY' in row:
                    try:
                        qty = int(row['QTY'])
                    except:
                        qty = 1
                elif 'ORIGINAL QTY' in row:
                    try:
                        qty = int(row['ORIGINAL QTY'])
                    except:
                        qty = 1
                total_qty += qty
            
            if total_qty > 0:
                avg_cost = total_client_cost_header / total_qty
                print(f"DEBUG: Calculated avg_cost = ${avg_cost:.2f} (total: ${total_client_cost_header} / qty: {total_qty})")
        
        # Use extracted LOT # as the lot_number for storage
        final_lot_number = extracted_lot_number if extracted_lot_number else lot_number
        
        print(f"DEBUG: FINAL VALUES BEFORE INSERT:")
        print(f"  - extracted_lot_number: '{extracted_lot_number}'")
        print(f"  - lot_number (fallback): '{lot_number}'")
        print(f"  - final_lot_number: '{final_lot_number}'")
        print(f"  - bol_location: '{bol_location}'")
        print(f"  - total_client_cost_header: {total_client_cost_header}")
        
        # Check if this lot already exists
        from rawbol_manager import check_lot_exists
        lot_check = check_lot_exists(final_lot_number)
        
        if lot_check.get('exists'):
            status_msg = []
            if lot_check.get('synced'):
                status_msg.append('already synced to bol.db')
            if lot_check.get('in_rawbol'):
                status_msg.append('already in rawbol.db')
            return {
                'success': False, 
                'error': f'LOT # "{final_lot_number}" has already been uploaded ({", ".join(status_msg)}). Please delete it first if you want to re-upload.',
                'lot_number': final_lot_number,
                'duplicate': True
            }
        
        # Insert into rawbol.db
        result = insert_raw_bol_items(df, final_lot_number, import_date, avg_cost, bol_location)
        
        if result.get('success'):
            # Log the upload with extracted header data and shipping cost
            log_upload(filename, final_lot_number, import_date, result.get('inserted', 0), total_client_cost_header, bol_location, shipping_cost)
            # Include extracted data in response
            result['lot_number'] = final_lot_number
            result['extracted_lot_number'] = extracted_lot_number
            result['bol_location'] = bol_location
            result['total_client_cost'] = total_client_cost_header
            result['avg_cost'] = avg_cost
            result['shipping_cost'] = shipping_cost
        
        return result
    except Exception as e:
        return {'success': False, 'error': str(e)}


def _retail_safe_text(value):
    txt = str(value or '').strip()
    if txt.lower() in ('nan', 'none', 'null'):
        return ''
    return txt


def _retail_parse_money(value):
    txt = _retail_safe_text(value)
    if not txt:
        return None
    txt = txt.replace('$', '').replace(',', '').strip()
    if txt.startswith('(') and txt.endswith(')'):
        txt = '-' + txt[1:-1]
    try:
        n = float(txt)
    except Exception:
        return None
    if n <= 0:
        return None
    return round(n, 2)


def _retail_normalize_upc_key(value):
    upc = _retail_safe_text(value)
    if not upc:
        return ''
    if upc.endswith('.0') and upc[:-2].isdigit():
        upc = upc[:-2]
    if '-' in upc:
        base, suffix = upc.split('-', 1)
        base = base.strip()
        suffix = suffix.strip()
    else:
        base, suffix = upc.strip(), ''
    if base.isdigit():
        base = base.lstrip('0') or '0'
    return (f'{base}-{suffix}' if suffix else base).strip().lower()


def _retail_find_upc_row(df_raw):
    for idx, row in df_raw.iterrows():
        for cell in row.values:
            if _retail_safe_text(cell).upper() == 'UPC':
                return int(idx)
    return None


def _retail_extract_lot(df_raw, upc_row_index):
    location_row_index = None
    for idx in range(upc_row_index):
        first_cell = _retail_safe_text(df_raw.iloc[idx, 0]).upper() if df_raw.shape[1] > 0 else ''
        if first_cell == 'LOCATION':
            location_row_index = idx
            break

    if location_row_index is not None:
        lot_col_idx = None
        location_row = df_raw.iloc[location_row_index]
        for col_idx, cell in enumerate(location_row.values):
            cell_str = _retail_safe_text(cell).upper()
            if 'LOT' in cell_str and ('#' in cell_str or 'NUMBER' in cell_str or cell_str.endswith('NO') or cell_str.endswith('NO.')):
                lot_col_idx = col_idx
                break
        if lot_col_idx is not None and location_row_index + 1 < upc_row_index:
            lot_value = _retail_safe_text(df_raw.iloc[location_row_index + 1, lot_col_idx])
            if lot_value:
                return lot_value

    lot_pattern = re.compile(r'\bLOT\b\s*(?:#|NO\.?|NUMBER)?\s*[:\-]?\s*([A-Za-z0-9\-_./]+)', re.IGNORECASE)
    for idx in range(upc_row_index):
        row = df_raw.iloc[idx]
        for cell in row.values:
            txt = _retail_safe_text(cell)
            if not txt:
                continue
            m = lot_pattern.search(txt)
            if m:
                candidate = _retail_safe_text(m.group(1))
                if _retail_is_plausible_lot(candidate):
                    return candidate
    return ''


def _retail_choose_col(columns):
    normalized_cols = {str(col).strip().lower(): col for col in columns}
    preferred_cols = (
        'original retail',
        'original_retail',
        'original retail price',
        'original_retail_price',
        'original retail $',
        'retail price',
        'retail_price'
    )
    for key in preferred_cols:
        if key in normalized_cols:
            return normalized_cols[key]

    for key, original_name in normalized_cols.items():
        compact = ''.join(ch for ch in key if ch.isalnum())
        if 'original' in compact and 'retail' in compact:
            return original_name

    for key, original_name in normalized_cols.items():
        if 'retail' in key and 'qty' not in key and 'quantity' not in key:
            return original_name
    return None


def _retail_extract_lot_from_text(raw_text):
    if not raw_text:
        return ''

    lines = [line.strip() for line in str(raw_text).split('\n') if line.strip()]
    for i, line in enumerate(lines):
        line_upper = line.upper().strip()
        if line_upper in ('LOT #', 'LOT#', 'LOT NUMBER', 'LOT NO', 'LOT NO.', 'LOT') or line_upper.startswith('LOT #:') or line_upper.startswith('LOT NUMBER:'):
            lot_match = line.split(':')[-1].strip() if ':' in line else ''
            if not lot_match or lot_match.upper().startswith('LOT'):
                if i + 1 < len(lines):
                    lot_match = lines[i + 1].strip()
            if _retail_is_plausible_lot(lot_match):
                return lot_match

    pattern = re.compile(r'\bLOT\b\s*(?:#|NO\.?|NUMBER)?\s*[:\-]?\s*([A-Za-z0-9\-_./]+)', re.IGNORECASE)
    for line in lines:
        m = pattern.search(line)
        if not m:
            continue
        candidate = _retail_safe_text(m.group(1))
        if _retail_is_plausible_lot(candidate):
            return candidate
    return ''


def _retail_is_plausible_lot(value):
    candidate = _retail_safe_text(value)
    if not candidate:
        return False
    upper = candidate.upper()
    if upper in ('LOT', 'NUMBER', '#', 'NO', 'NO.', 'H', 'HTML', 'HEAD', 'BODY'):
        return False
    if len(candidate) < 3 or len(candidate) > 50:
        return False
    if re.search(r'[A-Za-z0-9]', candidate) is None:
        return False
    return True


def process_bol_retail_backfill(file, overwrite_existing=False, forced_lot_number=None):
    """
    Parse one BOL file and backfill only raw_bol_items.original_retail for an existing LOT.
    No quantity or sync state changes are made.
    """
    try:
        file_bytes = file.read()
        if not file_bytes or len(file_bytes) < 10:
            return {'success': False, 'error': 'Uploaded file is empty or too small.'}

        filename = getattr(file, 'filename', None) or getattr(file, 'name', None) or ''
        ext = os.path.splitext(filename)[-1].lower()
        if ext not in ('.xls', '.xlsx', '.csv'):
            return {'success': False, 'error': 'Only .xls, .xlsx, and .csv files are supported.'}

        lot_number = _retail_safe_text(forced_lot_number)
        df = None

        # Handle "xls" files that are actually HTML exports (common in browser downloads).
        first_32 = file_bytes[:32].lower()
        html_indicators = [b'<html>', b'<html ', b'<!doct', b'<head>', b'<body>']
        is_html = any(indicator in first_32 for indicator in html_indicators)

        if is_html:
            try:
                html_content = file_bytes.decode('utf-8', errors='replace')
                tables = pd.read_html(StringIO(html_content))
                if not tables:
                    return {'success': False, 'error': 'No tables found in HTML file.'}

                df_raw = None
                for table in tables:
                    if any('UPC' in str(col).upper() for col in table.columns):
                        df_raw = table
                        break
                if df_raw is None:
                    for table in tables:
                        for col in table.columns:
                            col_values = table[col].dropna().astype(str)
                            normalized_values = [v.strip().replace('.0', '') for v in col_values]
                            upc_candidates = [v for v in normalized_values if v.isdigit() and 10 <= len(v) <= 14]
                            if len(col_values) > 0 and len(upc_candidates) > len(col_values) * 0.3:
                                df_raw = table
                                break
                        if df_raw is not None:
                            break
                if df_raw is None:
                    best_table = None
                    best_score = 0
                    for table in tables:
                        score = len(table.columns) * len(table)
                        if score > best_score and len(table.columns) > 2 and len(table) > 1:
                            best_score = score
                            best_table = table
                    df_raw = best_table
                if df_raw is None:
                    return {'success': False, 'error': 'Could not find item table in HTML file.'}

                if not any('UPC' in str(col).upper() for col in df_raw.columns):
                    for idx, row in df_raw.iterrows():
                        if any(str(cell).strip().upper() == 'UPC' for cell in row):
                            df_raw = df_raw.iloc[idx:].reset_index(drop=True)
                            df_raw.columns = df_raw.iloc[0]
                            df_raw = df_raw.iloc[1:].reset_index(drop=True)
                            break

                df = df_raw.copy()
                df.columns = [str(col).strip() for col in df.columns]

                if not lot_number:
                    lot_number = _retail_extract_lot_from_text(html_content)
            except Exception as e:
                return {'success': False, 'error': f'Failed to parse HTML-wrapped file: {e}'}
        else:
            in_memory_file = io.BytesIO(file_bytes)
            try:
                if ext == '.csv':
                    df_raw = pd.read_csv(in_memory_file, header=None, dtype=str)
                else:
                    engine = 'xlrd' if ext == '.xls' else 'openpyxl'
                    df_raw = pd.read_excel(in_memory_file, engine=engine, header=None, dtype=str)
            except Exception as read_err:
                msg = str(read_err)
                if 'Expected BOF record' in msg:
                    return {
                        'success': False,
                        'error': 'Unsupported format, or corrupt file. This looks like an HTML download renamed to .xls. Re-download as real Excel or use retail-only mode with a valid export.'
                    }
                return {'success': False, 'error': msg}

            upc_row_index = _retail_find_upc_row(df_raw)
            if upc_row_index is None:
                return {'success': False, 'error': 'Could not find row with "UPC" header.'}

            extracted_lot = _retail_extract_lot(df_raw, upc_row_index)
            if not lot_number:
                lot_number = extracted_lot

            in_memory_file.seek(0)
            if ext == '.csv':
                df = pd.read_csv(in_memory_file, header=upc_row_index, dtype=str)
            else:
                engine = 'xlrd' if ext == '.xls' else 'openpyxl'
                df = pd.read_excel(in_memory_file, engine=engine, header=upc_row_index, dtype=str)
            df.columns = [str(col).strip() for col in df.columns]

        if df is None:
            return {'success': False, 'error': 'Could not parse uploaded file.'}

        # LOT is optional for retail-only mode. If unavailable, fall back to UPC-only match.
        use_lot_scope = bool(lot_number)

        upc_col = None
        for col in df.columns:
            if str(col).strip().upper() == 'UPC':
                upc_col = col
                break
        if not upc_col:
            return {'success': False, 'error': 'Missing UPC column in item table.'}

        retail_col = _retail_choose_col(df.columns)
        if not retail_col:
            return {'success': False, 'error': 'Could not find original retail column in uploaded file.'}

        retail_map = {}
        for _, row in df.iterrows():
            upc_key = _retail_normalize_upc_key(row.get(upc_col))
            if not upc_key:
                continue
            retail_value = _retail_parse_money(row.get(retail_col))
            if retail_value is None:
                continue
            current = retail_map.get(upc_key)
            if current is None or current <= 0:
                retail_map[upc_key] = retail_value

        if not retail_map:
            return {'success': False, 'error': 'No valid UPC + retail rows found in uploaded file.', 'lot_number': lot_number}

        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rawbol.db')
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()

            cur.execute("PRAGMA table_info(raw_bol_items)")
            cols = [str(r[1]).lower() for r in cur.fetchall()]
            if 'original_retail' not in cols:
                cur.execute("ALTER TABLE raw_bol_items ADD COLUMN original_retail REAL")
                conn.commit()

            if use_lot_scope:
                cur.execute('''
                    SELECT id, upc, COALESCE(original_retail, 0)
                    FROM raw_bol_items
                    WHERE lot_number = ?
                ''', (lot_number,))
            else:
                cur.execute('''
                    SELECT id, upc, COALESCE(original_retail, 0)
                    FROM raw_bol_items
                ''')
            lot_rows = cur.fetchall()
            if not lot_rows:
                return {'success': False, 'error': 'No rows found in rawbol.db to update.'}

            lot_map = {}
            for row_id, upc, current_retail in lot_rows:
                key = _retail_normalize_upc_key(upc)
                if not key:
                    continue
                try:
                    current_val = round(float(current_retail or 0), 2)
                except Exception:
                    current_val = 0.0
                lot_map.setdefault(key, []).append((int(row_id), current_val))

            updated_rows = 0
            missing_upcs = 0
            skipped_existing_nonzero = 0

            for upc_key, retail_val in retail_map.items():
                matches = lot_map.get(upc_key)
                if not matches:
                    missing_upcs += 1
                    continue
                for row_id, current_val in matches:
                    if (not overwrite_existing) and current_val > 0:
                        skipped_existing_nonzero += 1
                        continue
                    cur.execute(
                        'UPDATE raw_bol_items SET original_retail = ? WHERE id = ?',
                        (retail_val, row_id)
                    )
                    updated_rows += 1

            conn.commit()
            return {
                'success': True,
                'mode': 'retail_backfill_only',
                'filename': filename,
                'lot_number': lot_number,
                'match_scope': ('lot+upc' if use_lot_scope else 'upc_only'),
                'source_upcs': len(retail_map),
                'updated_rows': updated_rows,
                'missing_upcs': missing_upcs,
                'skipped_existing_nonzero': skipped_existing_nonzero,
                'overwrite_existing': bool(overwrite_existing)
            }
        finally:
            conn.close()
    except Exception as e:
        return {'success': False, 'error': str(e)}
