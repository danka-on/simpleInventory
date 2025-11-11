import sqlite3

conn = sqlite3.connect('amazonStore.db')
cur = conn.cursor()

# Check one of the ASINs
asin = 'B00I4EMDUE'
cur.execute('SELECT ASIN, SKU, UPC, Title, upc_fetch_attempted, upc_last_fetch_date FROM ITEMS WHERE ASIN = ?', (asin,))
result = cur.fetchone()

if result:
    print(f"ASIN: {result[0]}")
    print(f"SKU: {result[1]}")
    print(f"UPC: {result[2]}")
    print(f"Title: {result[3]}")
    print(f"upc_fetch_attempted: {result[4]}")
    print(f"upc_last_fetch_date: {result[5]}")
else:
    print(f"ASIN {asin} not found in amazonStore")

# Check all 7 ASINs
asins = ['B00I4EMDUE', 'B089TR3GL1', 'B0CBCV19TR', 'B0DX3VNNVQ', 'B0BSN3S8TD', 'B002EI2XS8', 'B07B5SVLN8']
print(f"\n\nChecking all {len(asins)} ASINs:")
for asin in asins:
    cur.execute('SELECT UPC, upc_fetch_attempted FROM ITEMS WHERE ASIN = ?', (asin,))
    r = cur.fetchone()
    if r:
        print(f"{asin}: UPC={r[0]}, fetch_attempted={r[1]}")
    else:
        print(f"{asin}: NOT IN amazonStore.db")

conn.close()
