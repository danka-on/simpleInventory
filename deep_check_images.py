"""
Deep check of IMAGE column data types in amazonStore.db
"""
import sqlite3
import pandas as pd

amazon = sqlite3.connect('amazonStore.db')

# Use pandas to see actual data types
df = pd.read_sql("SELECT ASIN, UPC, TITLE, IMAGE FROM ITEMS LIMIT 20", amazon)

print("🔍 First 20 Amazon items IMAGE column inspection:")
print("=" * 80)
for idx, row in df.iterrows():
    image_val = row['IMAGE']
    image_type = type(image_val).__name__
    is_nan = pd.isna(image_val)
    
    if not is_nan and image_val:
        image_display = (str(image_val)[:60] + "...") if len(str(image_val)) > 60 else str(image_val)
    else:
        image_display = "NULL/NaN"
    
    print(f"{row['ASIN']} | Type: {image_type:10s} | isna: {is_nan} | Value: {image_display}")

amazon.close()

print("\n💡 If you see float 'nan' values, we need to handle pandas NaN differently")
