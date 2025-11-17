import sqlite3
from datetime import datetime

conn = sqlite3.connect('sync_settings.db')
cur = conn.cursor()

# Get settings
cur.execute('SELECT value FROM sync_status WHERE key = ?', ('sync_interval',))
row = cur.fetchone()
sync_interval = int(row[0]) if row else 60

cur.execute('SELECT value FROM sync_status WHERE key = ?', ('last_auto_sync',))
row = cur.fetchone()
last_sync = row[0] if row else None

cur.execute('SELECT value FROM sync_status WHERE key = ?', ('auto_sync_enabled',))
row = cur.fetchone()
auto_sync_enabled = row and row[0] == 'true'

conn.close()

print("\n" + "="*60)
print("SYNC TIMING ANALYSIS")
print("="*60)
print(f"\nAuto-sync enabled: {auto_sync_enabled}")
print(f"Sync interval: {sync_interval} minutes ({sync_interval/60:.1f} hours)")

if last_sync:
    last_sync_dt = datetime.fromisoformat(last_sync)
    now = datetime.now()
    time_since_sync = (now - last_sync_dt).total_seconds() / 60  # minutes
    time_until_next = sync_interval - time_since_sync
    
    print(f"\nLast auto-sync: {last_sync_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Time since last sync: {time_since_sync:.1f} minutes ({time_since_sync/60:.2f} hours)")
    print(f"Time until next sync: {time_until_next:.1f} minutes ({time_until_next/60:.2f} hours)")
    
    if time_since_sync < sync_interval:
        print(f"\n✅ TIMING IS CORRECT - Next sync in {time_until_next:.1f} minutes")
    else:
        print(f"\n⚠️  OVERDUE - Should have synced {abs(time_until_next):.1f} minutes ago")
else:
    print("\n⚠️  No sync has run yet")

print("\n" + "="*60)
