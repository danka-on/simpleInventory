# BOL Header Data Extraction

## Overview
The BOL extractor has been enhanced to extract metadata from the header section of BOL Excel files **before** the UPC table begins. This captures critical business information that was previously ignored.

## Extracted Data

### 1. LOT Number (BOL #)
- **Search Pattern:** Looks for cells containing "LOT", "LOT #", "LOT NUMBER", or "LOT NO"
- **Value Location:** Extracts the value from the adjacent cell (typically next column)
- **Purpose:** Provides the document's official lot identifier for cross-reference
- **Display:** Shown in upload success message and can be used for validation

### 2. Total Client Cost
- **Search Pattern:** Looks for cells containing "TOTAL CLIENT COST" (case-insensitive)
- **Value Location:** Extracts monetary value from adjacent cell
- **Cleaning:** Automatically removes $ signs and commas, converts to float
- **Purpose:** Captures the overall cost summary from the BOL document header
- **Display:** Shown in upload success message and Upload History table

## Implementation Details

### Database Changes
**Table:** `upload_logs` in `rawbol.db`
- **New Column:** `total_client_cost REAL`
- **Migration:** Automatic column addition on first run if missing
- **Storage:** Stores the extracted total client cost value for each upload

### Code Changes

#### 1. BOLextractor.py
```python
# Extracts header data before processing UPC table
# Supports both Excel and HTML file formats
# Returns extracted values in result dictionary:
{
    'success': True,
    'inserted': n,
    'extracted_lot_number': 'value or None',
    'total_client_cost': float or None
}
```

**Extraction Logic:**
- For Excel files: Scans rows 0 to UPC_row_index
- For HTML files: Parses text content before table
- Looks for keywords in cells/text
- Checks adjacent cells/lines for values

#### 2. rawbol_manager.py
**Modified Functions:**
- `ensure_rawbol_db()`: Added total_client_cost column to upload_logs table
- `log_upload()`: Now accepts `total_client_cost` parameter (optional, defaults to None)
- `get_upload_logs()`: Returns total_client_cost via `ul.*` in SELECT statement

#### 3. templates/extractor.html
**UI Enhancements:**
- Upload History table now includes "Total Client Cost" column
- Success message displays extracted LOT # and Total Client Cost
- Formatted with currency symbol and proper decimal places

## Data Flow

```
1. Upload BOL Excel/HTML file
   ↓
2. BOLextractor.py scans header section (rows before UPC table)
   ↓
3. Extracts LOT # and TOTAL CLIENT COST
   ↓
4. Processes UPC table data as before
   ↓
5. Stores items in raw_bol_items table
   ↓
6. Logs upload with metadata: log_upload(filename, lot_number, import_date, rows_imported, total_client_cost)
   ↓
7. Returns extracted data to frontend
   ↓
8. Frontend displays extracted values in success message
   ↓
9. Upload History shows total_client_cost for all uploads
```

## Testing

Use the test script to analyze your BOL file structure:

```powershell
python test_bol_header_extraction.py path/to/your/bol_file.xlsx
```

This will show:
- All rows before the UPC table
- Where LOT # patterns are found
- Where TOTAL CLIENT COST patterns are found
- Suggested extraction locations

## Error Handling

- If LOT # is not found: `extracted_lot_number` will be `None` (non-fatal)
- If TOTAL CLIENT COST is not found: `total_client_cost` will be `None` (non-fatal)
- If value cannot be converted to float: Error logged to console, value remains `None`
- Upload process continues even if header extraction fails
- Display shows "—" for missing values in the UI

## Benefits

1. **Data Validation:** Compare uploaded lot_number with extracted LOT # to catch mismatches
2. **Cost Verification:** Verify the header total matches the sum of item costs
3. **Audit Trail:** Full metadata captured for each BOL upload
4. **Business Intelligence:** Total cost data available for reporting and analysis
5. **Historical Record:** Upload history table shows cost totals for all past uploads

## Future Enhancements

Potential additions:
- Extract vendor name/ID
- Extract purchase order number
- Extract ship date
- Extract warehouse/destination
- Validation warnings if extracted LOT # differs from user input
- Validation warnings if header total doesn't match calculated item total
