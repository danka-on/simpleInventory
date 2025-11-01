"""
Enrich sold.db barcodes from all available sources.
Priority: ebayStore/amazonStore > rawbol.db > bol.db
"""
import sqlite3
import re

def clean_title_for_matching(title):
    """Clean title for fuzzy matching"""
    if not title:
        return ""
    # Remove common words and special chars
    title = title.lower()
    title = re.sub(r'[^a-z0-9\s]', ' ', title)
    title = re.sub(r'\s+', ' ', title).strip()
    # Remove common filler words
    stop_words = ['the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by']
    words = [w for w in title.split() if w not in stop_words]
    return ' '.join(words[:5])  # First 5 meaningful words

def enrich_barcodes():
    """Enrich sold.db barcodes from multiple sources"""
    
    # Connect to all databases
    sold_conn = sqlite3.connect('sold.db')
    sold_conn.row_factory = sqlite3.Row
    sold_cur = sold_conn.cursor()
    
    ebay_conn = sqlite3.connect('ebayStore.db')
    ebay_conn.row_factory = sqlite3.Row
    ebay_cur = ebay_conn.cursor()
    
    amazon_conn = sqlite3.connect('amazonStore.db')
    amazon_conn.row_factory = sqlite3.Row
    amazon_cur = amazon_conn.cursor()
    
    rawbol_conn = sqlite3.connect('rawbol.db')
    rawbol_conn.row_factory = sqlite3.Row
    rawbol_cur = rawbol_conn.cursor()
    
    bol_conn = sqlite3.connect('bol.db')
    bol_conn.row_factory = sqlite3.Row
    bol_cur = bol_conn.cursor()
    
    print("\n=== Enriching Sold Orders with Barcodes ===\n")
    
    # Get orders with missing barcodes
    sold_cur.execute('''
        SELECT order_id, item_id, title, store
        FROM orders 
        WHERE barcode IS NULL OR barcode = ""
        ORDER BY paid_time DESC
    ''')
    
    missing_orders = sold_cur.fetchall()
    total_missing = len(missing_orders)
    
    print(f"📊 Found {total_missing} orders missing barcodes\n")
    
    enriched_count = 0
    sources = {'ebayStore': 0, 'amazonStore': 0, 'rawbol': 0, 'bol': 0}
    
    for idx, order in enumerate(missing_orders, 1):
        order_id = order['order_id']
        item_id = order['item_id']
        title = order['title']
        store = order['store']
        
        barcode_found = None
        source = None
        
        # Priority 1: Try store database by ItemID/ASIN
        if store == 'ebay':
            ebay_cur.execute('SELECT UPC FROM INVENTORY WHERE ItemID = ? COLLATE NOCASE', (item_id,))
            ebay_row = ebay_cur.fetchone()
            if ebay_row and ebay_row['UPC']:
                barcode_found = ebay_row['UPC']
                source = 'ebayStore'
        
        elif store == 'amazon':
            amazon_cur.execute('SELECT UPC FROM ITEMS WHERE ASIN = ? COLLATE NOCASE', (item_id,))
            amazon_row = amazon_cur.fetchone()
            if amazon_row and amazon_row['UPC']:
                barcode_found = amazon_row['UPC']
                source = 'amazonStore'
        
        # Priority 2: Try rawbol.db by title matching
        if not barcode_found and title:
            cleaned_title = clean_title_for_matching(title)
            if cleaned_title:
                # Search in rawbol for similar titles
                rawbol_cur.execute('SELECT upc, item_description FROM raw_bol_items')
                for bol_item in rawbol_cur.fetchall():
                    bol_desc = clean_title_for_matching(bol_item['item_description'])
                    if bol_desc and cleaned_title in bol_desc or bol_desc in cleaned_title:
                        barcode_found = bol_item['upc']
                        source = 'rawbol'
                        break
        
        # Priority 3: Try bol.db by title matching
        if not barcode_found and title:
            cleaned_title = clean_title_for_matching(title)
            if cleaned_title:
                bol_cur.execute('SELECT upc, item_description FROM bol_items')
                for bol_item in bol_cur.fetchall():
                    bol_desc = clean_title_for_matching(bol_item['item_description'])
                    if bol_desc and cleaned_title in bol_desc or bol_desc in cleaned_title:
                        barcode_found = bol_item['upc']
                        source = 'bol'
                        break
        
        if barcode_found:
            # Update the barcode
            sold_cur.execute('UPDATE orders SET barcode = ? WHERE order_id = ?', (barcode_found, order_id))
            enriched_count += 1
            sources[source] += 1
            
            title_preview = title[:40] if title else 'N/A'
            print(f"✅ [{idx}/{total_missing}] {order_id} | {barcode_found} | {title_preview}... (from {source})")
        else:
            if idx <= 10:  # Only show first 10 failures
                title_preview = title[:40] if title else 'N/A'
                print(f"❌ [{idx}/{total_missing}] {order_id} | {title_preview}...")
    
    # Commit changes
    sold_conn.commit()
    
    print(f"\n{'='*70}")
    print(f"✅ Enrichment Complete!")
    print(f"   Total processed: {total_missing}")
    print(f"   Successfully enriched: {enriched_count}")
    print(f"   Still missing: {total_missing - enriched_count}")
    
    if enriched_count > 0:
        print(f"\n📊 Sources breakdown:")
        for source, count in sources.items():
            if count > 0:
                print(f"   {source}: {count}")
    
    # Close connections
    sold_conn.close()
    ebay_conn.close()
    amazon_conn.close()
    rawbol_conn.close()
    bol_conn.close()

if __name__ == '__main__':
    enrich_barcodes()
