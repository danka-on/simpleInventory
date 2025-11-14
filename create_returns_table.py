#!/usr/bin/env python3
"""
Create returns table in sold.db
"""

import sqlite3

conn = sqlite3.connect('sold.db')
cur = conn.cursor()

# Create returns table
cur.execute('''
CREATE TABLE IF NOT EXISTS returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_order_id INTEGER,
    order_id TEXT,
    item_id TEXT,
    barcode TEXT,
    title TEXT,
    quantity INTEGER,
    
    -- Financial Data
    original_price REAL,
    refund_amount REAL,
    original_shipping_cost REAL,
    return_shipping_cost REAL,
    original_seller_fee REAL,
    seller_fee_refund REAL,
    
    -- Dates
    return_date TEXT,
    received_date TEXT,
    
    -- Status
    store TEXT DEFAULT 'amazon',
    return_reason TEXT,
    condition_received TEXT,
    restocked BOOLEAN DEFAULT 0,
    
    -- Metadata
    lot_number TEXT,
    location TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
''')

conn.commit()
conn.close()

print('✅ Returns table created successfully in sold.db')
