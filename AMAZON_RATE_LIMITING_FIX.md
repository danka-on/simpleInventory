# Amazon Catalog API Rate Limiting Fix

## Problem
Getting "QuotaExceeded" error on first run when syncing 371 Amazon listings:
```
❌ Amazon Catalog API error for B0DX3VNNVQ: [{'code': 'QuotaExceeded', ...}]
```

## Root Cause
- Amazon Catalog API has **strict rate limits** (~1-2 requests per second)
- When syncing listings, the code fetches images from Catalog API for items without images
- Even on first run, making rapid requests exceeds the **burst rate limit**
- This is NOT a daily quota issue - it's a **requests per second** limit

## Solution Implemented

### 1. Added Rate Limiting (amazon_manager.py)
```python
# In __init__:
self.last_catalog_call_time = 0
self.catalog_api_delay = 0.6  # 600ms = ~1.67 requests/second (safe)

# In get_catalog_item():
current_time = time.time()
time_since_last_call = current_time - self.last_catalog_call_time

if time_since_last_call < self.catalog_api_delay:
    sleep_time = self.catalog_api_delay - time_since_last_call
    time.sleep(sleep_time)

self.last_catalog_call_time = time.time()
```

### 2. Added Usage Tracking
- Counts how many Catalog API calls are made during sync
- Displays in sync summary: `Catalog API calls: X (rate limited)`

### 3. Set Conservative Rate
- **600ms delay** between calls = ~1.67 requests/second
- Amazon's limit is likely 2 req/sec, so this provides safety margin
- Prevents quota errors while staying productive

## Impact

### Before Fix:
- ❌ Rapid fire requests → QuotaExceeded error
- ❌ Sync fails partway through
- ❌ No visibility into API usage

### After Fix:
- ✅ Automatic rate limiting prevents quota errors
- ✅ Sync completes successfully (just takes longer)
- ✅ Shows exactly how many Catalog API calls were made
- ✅ Safe for 371+ listings

## Example Output

```
✅ Retrieved 371 active listings
🔄 Starting Amazon order sync...
✅ Amazon listings sync complete:
   - New: 125 items
   - Updated: 246 items
   - Total: 371 items
   - Catalog API calls: 48 (rate limited)  ← NEW
```

## Performance Note

If 48 items need Catalog API calls:
- **Before**: Instant → Error after ~10 calls
- **After**: 48 × 0.6s = ~29 seconds → Success

This is acceptable since it only fetches images for listings without them (typically a minority).

## Testing

Run: `python test_rate_limiting.py`

This tests 3 ASINs with rate limiting and confirms timing is correct.

## Future Optimizations

1. **Cache images** in database to avoid re-fetching
2. **Batch process** only listings that changed
3. **Skip Catalog API** if image exists in rawbol.db
4. **Increase delay** if still getting quota errors (try 1.0s = 1 req/sec)

## When You Still Get Quota Errors

If you still see quota errors after this fix:

1. **Increase the delay**:
   ```python
   self.catalog_api_delay = 1.0  # More conservative: 1 req/sec
   ```

2. **Check your Amazon API usage**:
   - Login to Amazon Seller Central
   - Go to Developer Settings → API usage
   - Verify your Catalog API quota limits

3. **Skip image fetching temporarily**:
   - Comment out the Catalog API call in sync_listings_to_db()
   - Use only rawbol.db images for now

## Changes Made
- ✅ `amazon_manager.py`: Added rate limiting to get_catalog_item()
- ✅ `amazon_manager.py`: Added usage tracking for Catalog API calls
- ✅ `test_rate_limiting.py`: Created test script
