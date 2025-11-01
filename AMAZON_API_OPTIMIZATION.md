# Amazon API Request Optimization Analysis

## Current State Issues

### 1. **No API Request Tracking**
The current `sync_missing_upcs()` function queries ALL items where `UPC = ASIN` or `UPC IS NULL` on EVERY sync:

```python
cur.execute('SELECT ASIN FROM ITEMS WHERE UPC = ASIN OR UPC IS NULL')
```

**Problem:** If an ASIN doesn't have a UPC in Amazon's catalog (e.g., books, media, certain categories), the system will:
- Query it every sync
- Waste API quota
- Get no result every time
- Repeat indefinitely

### 2. **No Failed Request Tracking**
When an API call fails or returns no UPC:
- The item stays in the "needs fetch" queue
- Next sync will try again
- No record of "already attempted, no UPC available"

### 3. **Current Detection:** 336 items need UPC fetch, only 7 successfully fetched
This suggests many ASINs don't have UPCs in Amazon's catalog.

## Proposed Solutions

### Solution 1: Add API Request Tracking Columns (RECOMMENDED)

Add two columns to `amazonStore.db.ITEMS`:

```sql
ALTER TABLE ITEMS ADD COLUMN upc_fetch_attempted INTEGER DEFAULT 0;
ALTER TABLE ITEMS ADD COLUMN upc_last_fetch_date TEXT;
```

**Logic:**
- `upc_fetch_attempted = 0` → Never attempted
- `upc_fetch_attempted = 1` → Attempted, but no UPC found (skip in future)
- `upc_fetch_attempted = 2` → Attempted, API error (retry later)
- `upc_last_fetch_date` → Track when last attempted (for retry logic)

**Updated Query:**
```python
# Only fetch items that haven't been attempted OR failed more than 7 days ago
cur.execute('''
    SELECT ASIN 
    FROM ITEMS 
    WHERE (UPC = ASIN OR UPC IS NULL)
    AND (
        upc_fetch_attempted = 0 
        OR (upc_fetch_attempted = 2 AND upc_last_fetch_date < date('now', '-7 days'))
    )
''')
```

**Benefits:**
- Never retry items with no UPC available
- Retry failed API calls after cooldown
- Dramatically reduce API calls (likely 95%+ reduction)
- Track fetch history

### Solution 2: Create Separate Tracking Table

Create new table `amazon_api_log`:

```sql
CREATE TABLE amazon_api_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asin TEXT UNIQUE NOT NULL,
    request_type TEXT NOT NULL,  -- 'upc_fetch', 'catalog_item', etc.
    last_attempted TEXT NOT NULL,
    attempt_count INTEGER DEFAULT 1,
    success INTEGER DEFAULT 0,    -- 0=no UPC found, 1=UPC found
    error_message TEXT
);
```

**Benefits:**
- Keeps ITEMS table clean
- More detailed logging
- Can track different API request types
- Better for debugging

**Drawbacks:**
- Additional join required
- More complex queries

### Solution 3: Hybrid Approach (BEST)

Combine both:
1. Add simple flag to ITEMS table for quick filtering
2. Create detailed log table for analytics

## Recommended Implementation

### Step 1: Database Migration
```python
def migrate_amazon_tracking():
    """Add UPC fetch tracking to amazonStore.db"""
    conn = sqlite3.connect('amazonStore.db')
    cur = conn.cursor()
    
    # Add tracking columns
    try:
        cur.execute('ALTER TABLE ITEMS ADD COLUMN upc_fetch_attempted INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    try:
        cur.execute('ALTER TABLE ITEMS ADD COLUMN upc_last_fetch_date TEXT')
    except sqlite3.OperationalError:
        pass
    
    conn.commit()
    conn.close()
```

### Step 2: Update sync_missing_upcs()
```python
def sync_missing_upcs():
    """Sync UPCs for Amazon items - with smart duplicate prevention"""
    import time
    from datetime import datetime, timedelta
    
    conn = sqlite3.connect('amazonStore.db')
    cur = conn.cursor()
    
    # Ensure tracking columns exist
    migrate_amazon_tracking()
    
    # Only fetch items that:
    # 1. Don't have UPC (UPC = ASIN or NULL)
    # 2. Haven't been successfully attempted (fetch_attempted != 1)
    # 3. Haven't failed recently (fetch_attempted != 2 OR last_fetch > 7 days ago)
    retry_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    
    cur.execute('''
        SELECT ASIN 
        FROM ITEMS 
        WHERE (UPC = ASIN OR UPC IS NULL)
        AND (
            upc_fetch_attempted IS NULL 
            OR upc_fetch_attempted = 0
            OR (upc_fetch_attempted = 2 AND (upc_last_fetch_date IS NULL OR upc_last_fetch_date < ?))
        )
    ''', (retry_date,))
    
    items_to_fetch = [row[0] for row in cur.fetchall()]
    
    if not items_to_fetch:
        print("✅ No items need UPC fetching")
        conn.close()
        return 0
    
    print(f"🔍 Found {len(items_to_fetch)} items to fetch (smart filtering applied)")
    
    amazon = AmazonManager()
    upcs_found = 0
    today = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    for i, asin in enumerate(items_to_fetch, 1):
        try:
            catalog_data = amazon.get_catalog_item(asin)
            
            if not catalog_data:
                # API error - mark for retry later
                cur.execute('''
                    UPDATE ITEMS 
                    SET upc_fetch_attempted = 2, upc_last_fetch_date = ? 
                    WHERE ASIN = ?
                ''', (today, asin))
                print(f"⚠️ [{i}/{len(items_to_fetch)}] {asin}: API error (will retry)")
                time.sleep(0.5)
                continue
            
            upc = None
            
            # Extract UPC (existing logic)
            if 'attributes' in catalog_data:
                attrs = catalog_data['attributes']
                if 'externally_assigned_product_identifier' in attrs:
                    for identifier in attrs['externally_assigned_product_identifier']:
                        if identifier.get('type') in ['upc', 'ean']:
                            upc = identifier.get('value')
                            break
            
            if not upc and 'identifiers' in catalog_data:
                identifiers = catalog_data['identifiers']
                if isinstance(identifiers, list):
                    for id_group in identifiers:
                        if 'identifiers' in id_group:
                            for identifier in id_group['identifiers']:
                                if identifier.get('identifierType') in ['UPC', 'EAN']:
                                    upc = identifier.get('identifier')
                                    break
            
            if upc:
                # UPC found - update and mark as successfully fetched
                cur.execute('''
                    UPDATE ITEMS 
                    SET UPC = ?, upc_fetch_attempted = 0, upc_last_fetch_date = ? 
                    WHERE ASIN = ?
                ''', (upc, today, asin))
                upcs_found += 1
                print(f"✅ [{i}/{len(items_to_fetch)}] {asin}: {upc}")
            else:
                # No UPC in catalog - mark as attempted (won't retry)
                cur.execute('''
                    UPDATE ITEMS 
                    SET upc_fetch_attempted = 1, upc_last_fetch_date = ? 
                    WHERE ASIN = ?
                ''', (today, asin))
                print(f"⚠️ [{i}/{len(items_to_fetch)}] {asin}: No UPC in catalog (won't retry)")
            
            time.sleep(0.5)  # Rate limiting
            
        except Exception as e:
            # Error - mark for retry
            cur.execute('''
                UPDATE ITEMS 
                SET upc_fetch_attempted = 2, upc_last_fetch_date = ? 
                WHERE ASIN = ?
            ''', (today, asin))
            print(f"❌ [{i}/{len(items_to_fetch)}] {asin}: Error - {e}")
            time.sleep(0.5)
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ UPC sync complete: {upcs_found}/{len(items_to_fetch)} UPCs found")
    print(f"💡 API requests saved: {len(items_to_fetch)} first run, future runs will skip {len(items_to_fetch) - upcs_found} items permanently")
    return upcs_found
```

## Expected Impact

### Before Implementation:
- **First sync:** 336 API requests
- **Second sync:** 336 API requests (same items)
- **Third sync:** 336 API requests (same items)
- **Total waste:** 100% of quota wasted on items without UPCs

### After Implementation:
- **First sync:** 336 API requests (necessary)
- **Second sync:** ~0-5 API requests (only new items or retry errors)
- **Third sync:** ~0-5 API requests
- **Quota saved:** 95%+ reduction in API calls

### Additional Benefits:
1. **Faster syncs** - Skip known no-UPC items
2. **Better rate limit compliance** - Fewer requests
3. **Audit trail** - Know what was attempted
4. **Smart retry** - Only retry errors, not no-results
5. **Scalable** - Works with thousands of items

## Implementation Priority: HIGH

Amazon has strict rate limits (typically 5-10 requests/second with burst allowance). With 336 items being queried repeatedly, you're:
- Wasting quota on items that will never have UPCs
- Potentially hitting rate limits
- Slowing down sync operations

**Recommendation:** Implement Solution 1 (Hybrid Approach) immediately.
