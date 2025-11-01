"""
Check migration status and database health
===========================================
"""
import sqlite3
import os
from datetime import datetime

def check_file(filename):
    """Check if a file exists and return its info"""
    if os.path.exists(filename):
        size_mb = os.path.getsize(filename) / (1024 * 1024)
        mtime = datetime.fromtimestamp(os.path.getmtime(filename))
        return {
            'exists': True,
            'size_mb': size_mb,
            'modified': mtime
        }
    return {'exists': False}

def count_items(db_path, table_name):
    """Count items in a database table"""
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cur.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        return f"Error: {e}"

def main():
    print("=" * 70)
    print("DATABASE STATUS CHECK")
    print("=" * 70)
    
    # Check rack.db
    print("\n📊 rack.db:")
    rack_info = check_file('rack.db')
    if rack_info['exists']:
        print(f"   ⚠️  EXISTS (should be archived after migration)")
        print(f"   Size: {rack_info['size_mb']:.2f} MB")
        print(f"   Modified: {rack_info['modified']}")
        try:
            count = count_items('rack.db', 'INVENTORY')
            print(f"   Items: {count}")
        except:
            pass
    else:
        print("   ✅ NOT FOUND (expected after migration)")
    
    # Check for archived rack.db
    archived_files = [f for f in os.listdir('.') if f.startswith('rack.db.archived-')]
    if archived_files:
        print(f"\n   📁 Found {len(archived_files)} archived rack.db file(s):")
        for f in archived_files:
            info = check_file(f)
            print(f"      - {f} ({info['size_mb']:.2f} MB)")
    
    # Check searchRack.db
    print("\n📊 searchRack.db:")
    search_info = check_file('searchRack.db')
    if search_info['exists']:
        print(f"   ✅ EXISTS (main database)")
        print(f"   Size: {search_info['size_mb']:.2f} MB")
        print(f"   Modified: {search_info['modified']}")
        try:
            count = count_items('searchRack.db', 'SEARCHRACK')
            print(f"   Items: {count}")
            
            # Check for enrichment data
            conn = sqlite3.connect('searchRack.db')
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM SEARCHRACK WHERE TITLE IS NOT NULL AND TRIM(TITLE) != ''")
            with_title = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM SEARCHRACK WHERE IMAGE IS NOT NULL AND TRIM(IMAGE) != ''")
            with_image = cur.fetchone()[0]
            conn.close()
            print(f"   Items with titles: {with_title}")
            print(f"   Items with images: {with_image}")
        except Exception as e:
            print(f"   ⚠️  Error reading: {e}")
    else:
        print("   ❌ NOT FOUND (this is your main database!)")
    
    # Check backups
    print("\n📦 Backups:")
    if os.path.exists('backups'):
        backup_files = [f for f in os.listdir('backups') if f.startswith('searchRack-day') and f.endswith('.db')]
        if backup_files:
            print(f"   ✅ Found {len(backup_files)}/10 backup slots")
            # Sort by modification time
            backups_with_time = []
            for f in backup_files:
                path = os.path.join('backups', f)
                info = check_file(path)
                backups_with_time.append((f, info))
            
            backups_with_time.sort(key=lambda x: x[1]['modified'], reverse=True)
            
            print("   Most recent backups:")
            for f, info in backups_with_time[:3]:
                print(f"      - {f}: {info['modified'].strftime('%Y-%m-%d %H:%M:%S')} ({info['size_mb']:.2f} MB)")
        else:
            print("   ⚠️  Backup folder exists but no backups found")
            print("   Run: python rotating_backup.py backup")
    else:
        print("   ⚠️  No backups folder found")
        print("   Run: python rotating_backup.py backup")
    
    # Check for pre-migration backups
    premig_files = [f for f in os.listdir('.') if '.pre-migration-' in f]
    if premig_files:
        print(f"\n📦 Pre-migration backups:")
        print(f"   Found {len(premig_files)} safety backup(s):")
        for f in premig_files:
            info = check_file(f)
            print(f"      - {f} ({info['size_mb']:.2f} MB)")
        print("   💡 Can be deleted after verifying migration success")
    
    # Check rawbol.db
    print("\n📊 rawbol.db:")
    rawbol_info = check_file('rawbol.db')
    if rawbol_info['exists']:
        print(f"   ✅ EXISTS")
        print(f"   Size: {rawbol_info['size_mb']:.2f} MB")
        try:
            count = count_items('rawbol.db', 'raw_bol_items')
            print(f"   Items: {count}")
        except Exception as e:
            print(f"   Items: {e}")
    else:
        print("   ⚠️  NOT FOUND (should exist for BOL uploads)")
    
    # Migration status
    print("\n" + "=" * 70)
    print("MIGRATION STATUS:")
    print("=" * 70)
    
    rack_exists = check_file('rack.db')['exists']
    search_exists = check_file('searchRack.db')['exists']
    backups_exist = os.path.exists('backups') and len([f for f in os.listdir('backups') if f.startswith('searchRack-day')]) > 0
    
    if not rack_exists and search_exists and backups_exist:
        print("✅ MIGRATION COMPLETE")
        print("   - rack.db archived ✅")
        print("   - searchRack.db is main database ✅")
        print("   - Backups are set up ✅")
    elif rack_exists and search_exists:
        print("⚠️  MIGRATION NOT STARTED")
        print("   - Both databases exist")
        print("   - Run: python migrate_to_single_db.py")
    elif search_exists and not backups_exist:
        print("⚠️  MIGRATION INCOMPLETE")
        print("   - searchRack.db exists ✅")
        print("   - Backups not set up ❌")
        print("   - Run: python rotating_backup.py backup")
        print("   - Run: .\\setup_daily_backup.ps1")
    else:
        print("❌ UNEXPECTED STATE")
        print("   - Check your database files")
    
    print("\n" + "=" * 70)

if __name__ == '__main__':
    main()
