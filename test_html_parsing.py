import pandas as pd
import os
from io import StringIO

# Test HTML parsing with the debug file
debug_dir = 'debug_uploads'
if os.path.exists(debug_dir):
    debug_files = [f for f in os.listdir(debug_dir) if f.endswith('.debug')]
    if debug_files:
        # Get the most recent debug file
        debug_files.sort(key=lambda x: os.path.getmtime(os.path.join(debug_dir, x)), reverse=True)
        debug_file = os.path.join(debug_dir, debug_files[0])

        print(f"Testing HTML parsing with: {debug_file}")

        # Read the HTML content
        with open(debug_file, 'r', encoding='utf-8', errors='replace') as f:
            html_content = f.read()

        print(f"HTML content length: {len(html_content)} characters")

        # Try to parse tables
        try:
            tables = pd.read_html(StringIO(html_content))
            print(f"Found {len(tables)} tables")

            for i, table in enumerate(tables):
                print(f"\n=== Table {i} ===")
                print(f"Shape: {table.shape[0]} rows, {table.shape[1]} columns")
                print(f"Columns: {list(table.columns)}")
                print("First few column values:")
                for col in table.columns[:5]:  # Show first 5 columns
                    sample_values = table[col].dropna().head(3).tolist()
                    print(f"  {col}: {sample_values}")

                # Check if this table has UPC data
                table_str = table.to_string().upper()
                has_upc = 'UPC' in table_str
                print(f"Contains 'UPC': {has_upc}")

                # Look for actual UPC-like patterns (long numbers)
                upc_candidates = []
                for col in table.columns:
                    col_values = table[col].dropna().astype(str)
                    for val in col_values:
                        val = val.strip()
                        if val.isdigit() and len(val) >= 10:  # UPC-like length
                            upc_candidates.append(val)
                            if len(upc_candidates) >= 3:  # Show first few
                                break
                    if upc_candidates:
                        break

                if upc_candidates:
                    print(f"✓ Found UPC-like values: {upc_candidates[:3]}")
                    print("This looks like the product table!")
                else:
                    print("No UPC-like values found")

        except Exception as e:
            print(f"Error parsing HTML: {e}")
    else:
        print("No debug files found")
else:
    print("Debug directory not found")