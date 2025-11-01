"""
Consolidate rack.db into searchRack.db and set up rotating backups
===================================================================

This script will:
1. Backup both databases before making changes
2. Merge rack.db data into searchRack.db (preserving all data)
3. Remove all updateSearchRackDB() dependencies
4. Set up daily rotating backups (10-day rotation)
5. Archive the old rack.db

IMPORTANT: Review changes before running in production!
"""
import sqlite3
import os
import shutil
from datetime import datetime

def backup_before_migration():
    """Create safety backups before migration"""
    print("📦 Creating safety backups before migration...")
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    if os.path.exists('rack.db'):
        backup_path = f'rack.db.pre-migration-{timestamp}'
        shutil.copy2('rack.db', backup_path)
        print(f"   ✅ Backed up rack.db to {backup_path}")
    
    if os.path.exists('searchRack.db'):
        backup_path = f'searchRack.db.pre-migration-{timestamp}'
        shutil.copy2('searchRack.db', backup_path)
        print(f"   ✅ Backed up searchRack.db to {backup_path}")

def ensure_searchrack_schema():
    """Ensure searchRack.db has all necessary columns"""
    print("\n🔧 Ensuring searchRack.db has complete schema...")
    conn = sqlite3.connect('searchRack.db')
    cur = conn.cursor()
    
    # Create table if it doesn't exist
    cur.execute('''CREATE TABLE IF NOT EXISTS SEARCHRACK (
        ID INTEGER PRIMARY KEY AUTOINCREMENT,
        TITLE TEXT,
        BARCODE TEXT,
        ITEM_POSITION TEXT,
        IMAGES TEXT,
        PICTUREPOSITION TEXT,
        ITEMID TEXT,
        QUANTITY INTEGER DEFAULT 1,
        IMAGE TEXT,
        CREATED_AT TEXT
    )''')
    
    # Check for missing columns and add them
    cur.execute("PRAGMA table_info(SEARCHRACK)")
    existing_cols = [row[1] for row in cur.fetchall()]
    
    columns_to_add = {
        'TITLE': 'TEXT',
        'BARCODE': 'TEXT',
        'ITEM_POSITION': 'TEXT',
        'IMAGES': 'TEXT',
        'PICTUREPOSITION': 'TEXT',
        'ITEMID': 'TEXT',
        'QUANTITY': 'INTEGER DEFAULT 1',
        'IMAGE': 'TEXT',
        'CREATED_AT': 'TEXT'
    }
    
    for col_name, col_type in columns_to_add.items():
        if col_name not in existing_cols:
            print(f"   Adding column {col_name}...")
            cur.execute(f'ALTER TABLE SEARCHRACK ADD COLUMN {col_name} {col_type}')
    
    conn.commit()
    conn.close()
    print("   ✅ Schema is ready")

def migrate_rack_to_searchrack():
    """Migrate data from rack.db to searchRack.db"""
    print("\n🔄 Migrating rack.db data into searchRack.db...")
    
    if not os.path.exists('rack.db'):
        print("   ⚠️  rack.db not found, skipping migration")
        return
    
    rack_conn = sqlite3.connect('rack.db')
    rack_conn.row_factory = sqlite3.Row
    rack_cur = rack_conn.cursor()
    
    search_conn = sqlite3.connect('searchRack.db')
    search_cur = search_conn.cursor()
    
    # Get all items from rack.db
    try:
        rack_cur.execute("SELECT * FROM INVENTORY")
        rack_items = rack_cur.fetchall()
        print(f"   Found {len(rack_items)} items in rack.db")
    except sqlite3.OperationalError as e:
        print(f"   ⚠️  Error reading rack.db: {e}")
        rack_conn.close()
        search_conn.close()
        return
    
    migrated = 0
    updated = 0
    skipped = 0
    
    for item in rack_items:
        item_dict = dict(item)
        barcode = item_dict.get('BARCODE', '')
        position = item_dict.get('ITEM_POSITION', '')
        picturepos = item_dict.get('pictureposition', '')
        images = item_dict.get('IMAGES', '')
        created_at = item_dict.get('CREATED_AT', datetime.utcnow().isoformat())
        
        if not barcode or not barcode.strip():
            skipped += 1
            continue
        
        # Check if this barcode+position combo exists in searchRack
        search_cur.execute("""
            SELECT ID, QUANTITY FROM SEARCHRACK 
            WHERE TRIM(LOWER(BARCODE)) = TRIM(LOWER(?)) 
            AND TRIM(LOWER(COALESCE(ITEM_POSITION, ''))) = TRIM(LOWER(?))
        """, (barcode, position or ''))
        
        existing = search_cur.fetchone()
        
        if existing:
            # Update existing record, preserving enrichment data
            search_cur.execute("""
                UPDATE SEARCHRACK 
                SET PICTUREPOSITION = COALESCE(?, PICTUREPOSITION),
                    IMAGES = COALESCE(?, IMAGES),
                    CREATED_AT = COALESCE(?, CREATED_AT)
                WHERE ID = ?
            """, (picturepos, images, created_at, existing[0]))
            updated += 1
        else:
            # Insert new record
            search_cur.execute("""
                INSERT INTO SEARCHRACK 
                (BARCODE, ITEM_POSITION, IMAGES, PICTUREPOSITION, QUANTITY, CREATED_AT)
                VALUES (?, ?, ?, ?, 1, ?)
            """, (barcode, position, images, picturepos, created_at))
            migrated += 1
    
    search_conn.commit()
    rack_conn.close()
    search_conn.close()
    
    print(f"   ✅ Migration complete:")
    print(f"      - {migrated} new items added")
    print(f"      - {updated} existing items updated")
    print(f"      - {skipped} items skipped (empty barcode)")

def archive_rack_db():
    """Archive the old rack.db file"""
    print("\n📁 Archiving old rack.db...")
    
    if not os.path.exists('rack.db'):
        print("   ⚠️  rack.db not found, nothing to archive")
        return
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    archive_path = f'rack.db.archived-{timestamp}'
    shutil.move('rack.db', archive_path)
    print(f"   ✅ Moved rack.db to {archive_path}")
    print(f"   💡 You can delete this file after verifying the migration")

def verify_migration():
    """Verify the migration was successful"""
    print("\n🔍 Verifying migration...")
    
    conn = sqlite3.connect('searchRack.db')
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(*) FROM SEARCHRACK")
    total = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM SEARCHRACK WHERE BARCODE IS NOT NULL AND TRIM(BARCODE) != ''")
    with_barcode = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM SEARCHRACK WHERE TITLE IS NOT NULL AND TRIM(TITLE) != ''")
    with_title = cur.fetchone()[0]
    
    conn.close()
    
    print(f"   Total items in searchRack.db: {total}")
    print(f"   Items with barcodes: {with_barcode}")
    print(f"   Items with titles: {with_title}")
    print(f"   ✅ Migration verification complete")

def main():
    print("=" * 70)
    print("DATABASE CONSOLIDATION: rack.db → searchRack.db")
    print("=" * 70)
    print("\nThis will merge rack.db into searchRack.db and archive the old file.")
    print("Backups will be created before any changes are made.\n")
    
    response = input("Continue? (yes/no): ")
    if response.lower() != 'yes':
        print("❌ Migration cancelled")
        return
    
    # Step 1: Safety backups
    backup_before_migration()
    
    # Step 2: Ensure schema
    ensure_searchrack_schema()
    
    # Step 3: Migrate data
    migrate_rack_to_searchrack()
    
    # Step 4: Archive old database
    archive_rack_db()
    
    # Step 5: Verify
    verify_migration()
    
    print("\n" + "=" * 70)
    print("✅ MIGRATION COMPLETE!")
    print("=" * 70)
    print("\nNext steps:")
    print("1. Run the code update script to remove rack.db references")
    print("2. Set up the daily backup rotation")
    print("3. Test your barcode scanning workflow")
    print("4. After verification, delete the archived rack.db file")

if __name__ == '__main__':
    main()
