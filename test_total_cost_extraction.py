"""
Test the updated TOTAL CLIENT COST extraction logic.
Tests that we correctly identify the LOCATION row and extract the total from that column.
"""
import pandas as pd
import io

print("=== Testing Updated TOTAL CLIENT COST Extraction ===\n")

# Create a mock BOL structure similar to what the user described
mock_data = {
    'Row': [
        'Header Info',
        'LOCATION header',
        'Data row 1',
        'Data row 2',
        'Total row',
        'UPC header',
        'Item 1',
        'Item 2'
    ],
    'Structure': [
        'Row 0: Some header info',
        'Row 1: LOCATION | ... | TOTAL CLIENT COST (overall)',
        'Row 2: Location A | ... | $5000',
        'Row 3: Location B | ... | $3000',
        'Row 4: TOTAL | ... | $8000 ← THIS IS WHAT WE WANT',
        'Row 5: UPC | ... | TOTAL CLIENT COST (per-item) ← IGNORE',
        'Row 6: 123456 | ... | $50',
        'Row 7: 789012 | ... | $75'
    ]
}

print("BOL Structure:")
for i, (row, desc) in enumerate(zip(mock_data['Row'], mock_data['Structure'])):
    print(f"{i}: {desc}")

print("\n" + "="*60)
print("Testing Extraction Logic")
print("="*60)

# Create a simulated DataFrame matching this structure
df_raw = pd.DataFrame([
    ['Header', 'Info', 'Here', 'LOT #:', '12345'],
    ['LOCATION', 'VENDOR', 'SHIP DATE', 'TOTAL CLIENT COST', 'OTHER'],
    ['Store A', 'Vendor X', '2025-01-01', 5000.00, 'data'],
    ['Store B', 'Vendor Y', '2025-01-02', 3000.00, 'data'],
    ['TOTAL', '', '', 8000.00, ''],
    ['UPC', 'ITEM DESCRIPTION', 'QUANTITY', 'TOTAL CLIENT COST', 'IMAGE'],
    ['123456', 'Item A', 10, 500.00, 'url1'],
    ['789012', 'Item B', 20, 1500.00, 'url2']
])

print("\nDataFrame structure:")
print(df_raw.head(8))
print(f"\nTotal rows: {len(df_raw)}")

# Simulate the extraction logic
print("\n" + "="*60)
print("Extraction Steps:")
print("="*60)

# Find UPC row
upc_row_index = None
for idx, row in df_raw.iterrows():
    if any(str(cell).strip().upper() == 'UPC' for cell in row):
        upc_row_index = idx
        print(f"\n1. Found UPC row at index: {upc_row_index}")
        break

if upc_row_index is None:
    print("ERROR: UPC row not found!")
else:
    # Find LOCATION row
    location_row_index = None
    for idx in range(upc_row_index):
        row = df_raw.iloc[idx]
        first_cell = str(row.iloc[0]).strip().upper()
        if first_cell == 'LOCATION':
            location_row_index = idx
            print(f"2. Found LOCATION row at index: {location_row_index}")
            break
    
    if location_row_index is None:
        print("ERROR: LOCATION row not found!")
    else:
        # Find TOTAL CLIENT COST column in LOCATION row
        location_row = df_raw.iloc[location_row_index]
        total_cost_col_idx = None
        
        for col_idx, cell in enumerate(location_row):
            cell_str = str(cell).strip().upper()
            if 'TOTAL' in cell_str and 'CLIENT' in cell_str and 'COST' in cell_str:
                total_cost_col_idx = col_idx
                print(f"3. Found TOTAL CLIENT COST column at index: {col_idx}")
                print(f"   Column header: '{cell}'")
                break
        
        if total_cost_col_idx is None:
            print("ERROR: TOTAL CLIENT COST column not found in LOCATION row!")
        else:
            # Go down to find the last value
            print(f"\n4. Scanning column {total_cost_col_idx} from row {location_row_index + 1} to {upc_row_index}:")
            total_client_cost_header = None
            
            for idx in range(location_row_index + 1, upc_row_index):
                cell_value = df_raw.iloc[idx, total_cost_col_idx]
                cell_str = str(cell_value).strip()
                print(f"   Row {idx}: '{cell_str}' (type: {type(cell_value).__name__})")
                
                if cell_str and cell_str.lower() != 'nan':
                    try:
                        # Try to convert to float
                        if isinstance(cell_value, (int, float)):
                            total_client_cost_header = float(cell_value)
                        else:
                            cell_str = cell_str.replace('$', '').replace(',', '').strip()
                            total_client_cost_header = float(cell_str)
                        print(f"      ✓ Valid value: ${total_client_cost_header:,.2f}")
                    except ValueError:
                        print(f"      ✗ Could not convert to float")
            
            print("\n" + "="*60)
            if total_client_cost_header:
                print(f"✅ SUCCESS: Extracted TOTAL CLIENT COST = ${total_client_cost_header:,.2f}")
                print(f"\nThis is from the LOCATION header row (overall BOL total)")
                print(f"NOT from the UPC header row (per-item cost)")
            else:
                print("❌ FAILED: Could not extract TOTAL CLIENT COST")
            print("="*60)

print("\n\nExpected Result: $8,000.00")
print("This is the sum of Location A ($5,000) + Location B ($3,000)")
print("\nNOTE: We ignore the per-item TOTAL CLIENT COST in the UPC row")
