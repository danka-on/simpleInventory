import os

picture_files = [
    "1760641866.jpg",
    "1760642422.jpg",
    "1748026709.jpg"
]

base_path = r"c:\Users\boxatron\Desktop\simpleInventory\static\pictureposition"

print("Checking if picture position files exist:")
print("=" * 60)

for pf in picture_files:
    full_path = os.path.join(base_path, pf)
    exists = os.path.exists(full_path)
    status = "✓ EXISTS" if exists else "✗ MISSING"
    print(f"{status}: {pf}")
    if exists:
        size = os.path.getsize(full_path)
        print(f"         Size: {size:,} bytes")

print("\n" + "=" * 60)
print("These files should load in searchrack if the format fix worked.")
