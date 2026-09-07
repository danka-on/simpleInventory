import os
import json

# Local-only inventory: we no longer use Google Sheets or gspread.
# Populate `inventory_cache.json` with a single-column list where shelf codes (a,b,c,d)
# appear on their own row followed by items that belong to that shelf. Example:
# ["a","12345","23456","b","98765", ...]

LOCAL_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'inventory_cache.json')
shelfCodes = ('a', 'b', 'c', 'd')


def _read_local_cache():
    """Return list of strings from the local cache file, or [] if missing/invalid."""
    if not os.path.exists(LOCAL_CACHE):
        return []
    try:
        with open(LOCAL_CACHE, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return [str(x) for x in data]
    except Exception as e:
        print(f"Warning: failed to read {LOCAL_CACHE}: {e}")
    return []


def update_inventory():
    """Build a dict mapping shelf code -> list of items from the local cache."""
    inventory = _read_local_cache()
    result = {k: [] for k in shelfCodes}
    currentShelf = None
    for raw in inventory:
        item = raw.strip()
        if not item:
            continue
        low = item.lower()
        if low in shelfCodes:
            currentShelf = low
            continue
        if currentShelf:
            result[currentShelf].append(item)
    return result


def find_item(barcode):
    """Return the shelf code (e.g., 'a') where barcode is found, or None."""
    if barcode is None:
        return None
    barcode = str(barcode).strip()
    currentInventory = update_inventory()
    for shelf, items in currentInventory.items():
        for it in items:
            if str(it).strip() == barcode:
                return shelf
    return None


def write_local_cache(values):
    """Write a list of strings to the local cache file (overwrites)."""
    try:
        with open(LOCAL_CACHE, 'w', encoding='utf-8') as fh:
            json.dump(list(values), fh)
        return True
    except Exception as e:
        print(f"Failed to write local cache: {e}")
        return False





