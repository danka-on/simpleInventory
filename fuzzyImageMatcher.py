import os
import sqlite3
import requests
from PIL import Image
import imagehash
from io import BytesIO

# Directories
IMAGE_DIR = 'matchImages'
os.makedirs(IMAGE_DIR, exist_ok=True)

# Connect to databases
bol_conn = sqlite3.connect('bol.db')
ebay_conn = sqlite3.connect('ebayStore.db')

# Fetch image URLs
bol_urls = set(row[0] for row in bol_conn.execute("SELECT image_url FROM bol_items") if row[0])
ebay_urls = set(row[0] for row in ebay_conn.execute("SELECT image FROM INVENTORY") if row[0])

all_urls = bol_urls.union(ebay_urls)

# Helper to get filename from URL
import hashlib
def url_to_filename(url):
    ext = os.path.splitext(url.split('?')[0])[1]
    h = hashlib.md5(url.encode()).hexdigest()
    return f"{h}{ext if ext else '.jpg'}"

# Download images if not already present
print(f"[1/3] Downloading images to {IMAGE_DIR}...")
total = len(all_urls)
for idx, url in enumerate(all_urls, 1):
    fname = url_to_filename(url)
    fpath = os.path.join(IMAGE_DIR, fname)
    percent = (idx / total) * 100
    if not os.path.exists(fpath):
        print(f"  [{percent:.1f}%] Downloading: {url}")
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            with open(fpath, 'wb') as f:
                f.write(resp.content)
        except Exception as e:
            print(f"    Failed to download {url}: {e}")
    else:
        print(f"  [{percent:.1f}%] Already downloaded: {url}")

# Build hash maps
print("[2/3] Hashing BOL images...")
bol_hashes = {}
total = len(bol_urls)
for idx, url in enumerate(bol_urls, 1):
    fname = url_to_filename(url)
    fpath = os.path.join(IMAGE_DIR, fname)
    percent = (idx / total) * 100
    try:
        img = Image.open(fpath)
        bol_hashes[url] = imagehash.phash(img)
        print(f"  [{percent:.1f}%] Hashed BOL image: {url}")
    except Exception as e:
        print(f"    Failed to hash {fpath}: {e}")

print("[2/3] Hashing eBay images...")
ebay_hashes = {}
total = len(ebay_urls)
for idx, url in enumerate(ebay_urls, 1):
    fname = url_to_filename(url)
    fpath = os.path.join(IMAGE_DIR, fname)
    percent = (idx / total) * 100
    try:
        img = Image.open(fpath)
        ebay_hashes[url] = imagehash.phash(img)
        print(f"  [{percent:.1f}%] Hashed eBay image: {url}")
    except Exception as e:
        print(f"    Failed to hash {fpath}: {e}")

# Match images
print("[3/3] Matching images...")
THRESHOLD = 0.60
matches = []
total = len(ebay_hashes)
for idx, (ebay_url, ebay_hash) in enumerate(ebay_hashes.items(), 1):
    best_match = None
    best_score = 0
    percent = (idx / total) * 100
    for bol_url, bol_hash in bol_hashes.items():
        dist = ebay_hash - bol_hash
        score = 1 - dist / len(ebay_hash.hash) ** 2
        if score > best_score:
            best_score = score
            best_match = bol_url
    print(f"  [{percent:.1f}%] Matched eBay image: {ebay_url}")
    if best_score >= THRESHOLD:
        matches.append((ebay_url, best_match, best_score))

print(f"\nDone! {len(matches)} matches found.")
# Print results
for ebay_url, bol_url, score in matches:
    print(f"eBay Image: {ebay_url}\nBOL Image: {bol_url}\nSimilarity: {score*100:.2f}%\n{'-'*40}")

bol_conn.close()
ebay_conn.close()
