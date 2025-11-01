"""
Add database indexes to frequently queried columns
This will improve query performance by 10-100x
"""
import sqlite3

def add_indexes_to_database(db_name, indexes):
    """Add indexes to a database if they don't already exist"""
    print(f'\n=== {db_name} ===')
    conn = sqlite3.connect(db_name)
    cur = conn.cursor()
    
    # Get existing indexes
    cur.execute("SELECT name FROM sqlite_master WHERE type='index'")
    existing_indexes = {row[0] for row in cur.fetchall()}
    
    added_count = 0
    skipped_count = 0
    
    for index_name, index_sql in indexes:
        if index_name in existing_indexes:
            print(f'  ⏭️  {index_name} (already exists)')
            skipped_count += 1
        else:
            try:
                cur.execute(index_sql)
                print(f'  ✅ {index_name}')
                added_count += 1
            except Exception as e:
                print(f'  ❌ {index_name}: {e}')
    
    conn.commit()
    conn.close()
    
    print(f'  Summary: {added_count} added, {skipped_count} skipped')
    return added_count

print('=== Adding Database Indexes for Performance ===')

# sold.db indexes
sold_indexes = [
    ('idx_orders_barcode', 'CREATE INDEX idx_orders_barcode ON orders(barcode)'),
    ('idx_orders_lot_number', 'CREATE INDEX idx_orders_lot_number ON orders(lot_number)'),
    ('idx_orders_paid_time', 'CREATE INDEX idx_orders_paid_time ON orders(paid_time)'),
    ('idx_orders_store', 'CREATE INDEX idx_orders_store ON orders(store)'),
    ('idx_orders_order_id', 'CREATE INDEX idx_orders_order_id ON orders(order_id)'),
]

# rawbol.db indexes
rawbol_indexes = [
    ('idx_raw_bol_items_upc', 'CREATE INDEX idx_raw_bol_items_upc ON raw_bol_items(upc)'),
    ('idx_raw_bol_items_lot_number', 'CREATE INDEX idx_raw_bol_items_lot_number ON raw_bol_items(lot_number)'),
    ('idx_raw_bol_items_created_at', 'CREATE INDEX idx_raw_bol_items_created_at ON raw_bol_items(created_at)'),
    ('idx_upload_logs_lot_number', 'CREATE INDEX idx_upload_logs_lot_number ON upload_logs(lot_number)'),
    ('idx_upload_logs_import_date', 'CREATE INDEX idx_upload_logs_import_date ON upload_logs(import_date)'),
]

# amazonStore.db indexes
amazon_indexes = [
    ('idx_items_upc', 'CREATE INDEX idx_items_upc ON ITEMS(UPC)'),
    ('idx_items_asin', 'CREATE INDEX idx_items_asin ON ITEMS(ASIN)'),
    ('idx_items_sku', 'CREATE INDEX idx_items_sku ON ITEMS(SKU)'),
    ('idx_items_status', 'CREATE INDEX idx_items_status ON ITEMS(STATUS)'),
    ('idx_items_upc_fetch_attempted', 'CREATE INDEX idx_items_upc_fetch_attempted ON ITEMS(upc_fetch_attempted)'),
]

# ebayStore.db indexes
ebay_indexes = [
    ('idx_inventory_upc', 'CREATE INDEX idx_inventory_upc ON INVENTORY(UPC)'),
    ('idx_inventory_itemid', 'CREATE INDEX idx_inventory_itemid ON INVENTORY(ItemID)'),
    ('idx_inventory_sku', 'CREATE INDEX idx_inventory_sku ON INVENTORY(SKU)'),
]

# bol.db indexes
bol_indexes = [
    ('idx_bol_items_upc', 'CREATE INDEX idx_bol_items_upc ON bol_items(upc)'),
    ('idx_bol_items_lot_number', 'CREATE INDEX idx_bol_items_lot_number ON bol_items(lot_number)'),
    ('idx_bol_items_list_status', 'CREATE INDEX idx_bol_items_list_status ON bol_items(list_status)'),
]

# Add indexes to each database
total_added = 0
total_added += add_indexes_to_database('sold.db', sold_indexes)
total_added += add_indexes_to_database('rawbol.db', rawbol_indexes)
total_added += add_indexes_to_database('amazonStore.db', amazon_indexes)
total_added += add_indexes_to_database('ebayStore.db', ebay_indexes)
total_added += add_indexes_to_database('bol.db', bol_indexes)

print(f'\n🎉 Complete! Added {total_added} new indexes')
print('\n✅ Expected Performance Improvements:')
print('   - UPC lookups: 10-100x faster')
print('   - LOT number queries: 10-50x faster')
print('   - Date range queries: 5-20x faster')
print('   - Status filtering: 5-10x faster')
print('\n💡 Queries on indexed columns will now use binary search instead of full table scans!')
