"""
Daily Rotating Backup System for searchRack.db
===============================================

Creates daily backups with a 10-day rotation:
- Backups stored in backups/ folder
- Named: searchRack-day1.db, searchRack-day2.db, ... searchRack-day10.db
- Day 11 overwrites day 1, day 12 overwrites day 2, etc.
- Keeps exactly 10 days of history

Run this script daily (can be automated with Task Scheduler on Windows)
"""
import sqlite3
import os
import shutil
from datetime import datetime

BACKUP_DIR = 'backups'
DB_NAME = 'searchRack.db'
MAX_BACKUPS = 10

def ensure_backup_directory():
    """Create backups directory if it doesn't exist"""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
        print(f"✅ Created {BACKUP_DIR}/ directory")

def get_current_day_slot():
    """
    Get the current day's backup slot (1-10)
    Uses day of month modulo 10, so:
    - Days 1, 11, 21, 31 → slot 1
    - Days 2, 12, 22 → slot 2
    - etc.
    """
    day_of_month = datetime.now().day
    slot = ((day_of_month - 1) % MAX_BACKUPS) + 1
    return slot

def create_backup():
    """Create a rotating backup of searchRack.db"""
    if not os.path.exists(DB_NAME):
        print(f"❌ Error: {DB_NAME} not found!")
        return False
    
    ensure_backup_directory()
    
    # Get current day slot (1-10)
    slot = get_current_day_slot()
    backup_filename = f'searchRack-day{slot}.db'
    backup_path = os.path.join(BACKUP_DIR, backup_filename)
    
    # Check if we're overwriting an old backup
    if os.path.exists(backup_path):
        old_time = datetime.fromtimestamp(os.path.getmtime(backup_path))
        print(f"📝 Overwriting old backup from {old_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        # Copy the database file
        shutil.copy2(DB_NAME, backup_path)
        
        # Verify the backup is readable
        test_conn = sqlite3.connect(backup_path)
        test_cur = test_conn.cursor()
        test_cur.execute("SELECT COUNT(*) FROM SEARCHRACK")
        count = test_cur.fetchone()[0]
        test_conn.close()
        
        # Get file size
        size_mb = os.path.getsize(backup_path) / (1024 * 1024)
        
        print(f"✅ Backup created: {backup_filename}")
        print(f"   📊 {count} items backed up")
        print(f"   💾 Size: {size_mb:.2f} MB")
        print(f"   📅 Slot: {slot}/10 (today is day {datetime.now().day} of month)")
        
        return True
        
    except Exception as e:
        print(f"❌ Backup failed: {e}")
        return False

def list_backups():
    """List all existing backups"""
    if not os.path.exists(BACKUP_DIR):
        print("No backups directory found")
        return
    
    backups = []
    for i in range(1, MAX_BACKUPS + 1):
        filename = f'searchRack-day{i}.db'
        filepath = os.path.join(BACKUP_DIR, filename)
        if os.path.exists(filepath):
            mtime = os.path.getmtime(filepath)
            size = os.path.getsize(filepath) / (1024 * 1024)
            backups.append({
                'slot': i,
                'filename': filename,
                'date': datetime.fromtimestamp(mtime),
                'size_mb': size
            })
    
    if not backups:
        print("No backups found")
        return
    
    print(f"\n📦 Available Backups ({len(backups)}/10 slots):")
    print("-" * 70)
    for b in sorted(backups, key=lambda x: x['date'], reverse=True):
        print(f"   Day {b['slot']}: {b['filename']}")
        print(f"           Created: {b['date'].strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"           Size: {b['size_mb']:.2f} MB")
        print()

def restore_backup(slot):
    """Restore a backup from a specific slot"""
    backup_filename = f'searchRack-day{slot}.db'
    backup_path = os.path.join(BACKUP_DIR, backup_filename)
    
    if not os.path.exists(backup_path):
        print(f"❌ Backup slot {slot} not found!")
        return False
    
    # Create a safety backup of current database
    if os.path.exists(DB_NAME):
        safety_backup = f'{DB_NAME}.before-restore-{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        shutil.copy2(DB_NAME, safety_backup)
        print(f"📦 Created safety backup: {safety_backup}")
    
    try:
        # Restore the backup
        shutil.copy2(backup_path, DB_NAME)
        
        # Verify
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM SEARCHRACK")
        count = cur.fetchone()[0]
        conn.close()
        
        backup_date = datetime.fromtimestamp(os.path.getmtime(backup_path))
        print(f"✅ Restored backup from day {slot}")
        print(f"   📅 Backup date: {backup_date.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   📊 {count} items restored")
        
        return True
        
    except Exception as e:
        print(f"❌ Restore failed: {e}")
        return False

def main():
    import sys
    
    print("=" * 70)
    print("ROTATING BACKUP SYSTEM FOR searchRack.db")
    print("=" * 70)
    print(f"Maintains 10 days of backup history\n")
    
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        
        if command == 'backup':
            create_backup()
        elif command == 'list':
            list_backups()
        elif command == 'restore' and len(sys.argv) > 2:
            try:
                slot = int(sys.argv[2])
                if 1 <= slot <= MAX_BACKUPS:
                    restore_backup(slot)
                else:
                    print(f"❌ Slot must be between 1 and {MAX_BACKUPS}")
            except ValueError:
                print("❌ Invalid slot number")
        else:
            print("Usage:")
            print("  python rotating_backup.py backup          - Create a backup")
            print("  python rotating_backup.py list            - List all backups")
            print("  python rotating_backup.py restore <1-10>  - Restore from backup")
    else:
        # Default: create backup
        create_backup()
        print("\n" + "-" * 70)
        list_backups()

if __name__ == '__main__':
    main()
