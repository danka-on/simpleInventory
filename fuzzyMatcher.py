import sqlite3
from rapidfuzz import process

# Connect to the databases
bol_conn = sqlite3.connect('bol.db')
ebay_conn = sqlite3.connect('ebayStore.db')

# Fetch descriptions from bol.db
bol_descriptions = [row[0] for row in bol_conn.execute("SELECT item_description FROM bol_items")]

# Fetch titles from ebayStore.db
ebay_titles = [row[0] for row in ebay_conn.execute("SELECT title FROM INVENTORY")]

# Set threshold
THRESHOLD = 86

# Perform fuzzy matching
matches = []
count = 0
for ebay_title in ebay_titles:
    match, score, _ = process.extractOne(ebay_title, bol_descriptions)
    if score >= THRESHOLD:
        count += 1
        matches.append((ebay_title, match, score))

# Print results
for ebay_title, bol_description, score in matches:
    print(f"eBay Title: {ebay_title}\nBOL Description: {bol_description}\nScore: {score}\n{'-'*40}\nCount: {count}")

bol_conn.close()
ebay_conn.close()