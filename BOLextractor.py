import sqlite3
import pandas as pd
import io

def process_bol_excel(file, import_date):
    """
    Process the uploaded Excel file and import date, extract required columns, check for duplicates by UPC, and insert into SQLite DB (bol.db).
    Returns: {'success': True} or {'success': False, 'error': '...'}
    """
    try:
        # Read Excel file into DataFrame
        in_memory_file = io.BytesIO(file.read())
        df = pd.read_excel(in_memory_file)
        required_columns = ['UPC', 'ITEM DESCRIPTION', 'CLIENT COST', 'TOTAL CLIENT COST', 'IMAGE', 'LOT #', 'BOL #']
        for col in required_columns:
            if col not in df.columns:
                return {'success': False, 'error': f'Missing required column: {col}'}

        # Connect to bol.db and create table if not exists
        conn = sqlite3.connect('bol.db')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS bol_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upc TEXT UNIQUE,
            item_description TEXT,
            client_cost REAL,
            total_client_cost REAL,
            image_url TEXT,
            lot_number TEXT,
            bol_number TEXT,
            import_date TEXT
        )''')
        conn.commit()

        # Insert rows, skip if UPC already exists
        inserted = 0
        for _, row in df.iterrows():
            upc = str(row['UPC']).strip()
            if not upc or upc.lower() == 'nan':
                continue
            c.execute('SELECT 1 FROM bol_items WHERE upc = ?', (upc,))
            if c.fetchone():
                continue  # duplicate
            c.execute('''INSERT INTO bol_items (upc, item_description, client_cost, total_client_cost, image_url, lot_number, bol_number, import_date)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                      (upc,
                       str(row['ITEM DESCRIPTION']).strip(),
                       float(row['CLIENT COST']) if not pd.isna(row['CLIENT COST']) else None,
                       float(row['TOTAL CLIENT COST']) if not pd.isna(row['TOTAL CLIENT COST']) else None,
                       str(row['IMAGE']).strip(),
                       str(row['LOT #']).strip(),
                       str(row['BOL #']).strip(),
                       import_date))
            inserted += 1
        conn.commit()
        conn.close()
        return {'success': True, 'inserted': inserted}
    except Exception as e:
        return {'success': False, 'error': str(e)}