"""
Test script to examine BOL Excel structure and extract header data before UPC table.
This will help identify where LOT # and TOTAL CLIENT COST are located.
"""
import pandas as pd
import sys

def analyze_bol_structure(file_path):
    """Analyze the structure of a BOL Excel file before the UPC table."""
    print(f"=== Analyzing BOL file: {file_path} ===\n")
    
    try:
        # Read entire file without header
        df_raw = pd.read_excel(file_path, header=None)
        
        print(f"Total rows in file: {len(df_raw)}")
        print(f"Total columns: {len(df_raw.columns)}\n")
        
        # Find the UPC row
        upc_row_index = None
        for idx, row in df_raw.iterrows():
            if any(str(cell).strip().upper() == 'UPC' for cell in row):
                upc_row_index = idx
                print(f"Found UPC header at row {idx}\n")
                break
        
        if upc_row_index is None:
            print("ERROR: Could not find UPC header row")
            return
        
        # Display all rows BEFORE the UPC table
        print("=== DATA BEFORE UPC TABLE ===")
        print("=" * 100)
        
        for idx in range(min(upc_row_index, len(df_raw))):
            row = df_raw.iloc[idx]
            print(f"\nRow {idx}:")
            for col_idx, cell in enumerate(row):
                cell_str = str(cell).strip()
                if cell_str and cell_str != 'nan':
                    print(f"  Column {col_idx}: {cell_str}")
        
        print("\n" + "=" * 100)
        print("=== ANALYSIS ===")
        
        # Look for specific patterns
        lot_patterns = ['LOT', 'LOT #', 'LOT NUMBER', 'LOT NO']
        total_patterns = ['TOTAL CLIENT COST', 'TOTAL COST', 'CLIENT COST TOTAL']
        
        print("\nSearching for LOT # patterns...")
        for idx in range(min(upc_row_index, len(df_raw))):
            row = df_raw.iloc[idx]
            for col_idx, cell in enumerate(row):
                cell_str = str(cell).strip().upper()
                if any(pattern in cell_str for pattern in lot_patterns):
                    print(f"  ✓ Found at Row {idx}, Column {col_idx}: '{cell}'")
                    # Check adjacent cells for the actual lot number value
                    if col_idx + 1 < len(row):
                        next_cell = row.iloc[col_idx + 1]
                        print(f"    → Value might be in next column: '{next_cell}'")
        
        print("\nSearching for TOTAL CLIENT COST patterns...")
        for idx in range(min(upc_row_index, len(df_raw))):
            row = df_raw.iloc[idx]
            for col_idx, cell in enumerate(row):
                cell_str = str(cell).strip().upper()
                if any(pattern in cell_str for pattern in total_patterns):
                    print(f"  ✓ Found at Row {idx}, Column {col_idx}: '{cell}'")
                    # Check adjacent cells for the actual value
                    if col_idx + 1 < len(row):
                        next_cell = row.iloc[col_idx + 1]
                        print(f"    → Value might be in next column: '{next_cell}'")
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python test_bol_header_extraction.py <path_to_bol_excel_file>")
        print("\nPlease provide a sample BOL Excel file to analyze.")
        sys.exit(1)
    
    analyze_bol_structure(sys.argv[1])
