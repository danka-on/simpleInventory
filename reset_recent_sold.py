import sqlite3
import datetime

# Configuration
LOOKBACK_HOURS = 48  # consider shipped orders within last N hours
MAX_RESET = 10       # max number of orders to reset
FORCE_WITHIN_GRACE = False  # if True, set shipped_time to now-24h for reset rows (demo)


def ensure_columns(cur):
    cur.execute('PRAGMA table_info(orders)')
    cols = {r[1] for r in cur.fetchall()}
    if 'removal_cancelled' not in cols:
        cur.execute('ALTER TABLE orders ADD COLUMN removal_cancelled INTEGER DEFAULT 0')


def parse_iso(ts: str) -> datetime.datetime | None:
    if not ts:
        return None
    ts = ts.strip()
    try:
        if ts.endswith('Z'):
            dt = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
        else:
            dt = datetime.datetime.fromisoformat(ts)
        if dt.tzinfo:
            dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def main():
    conn = sqlite3.connect('sold.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    ensure_columns(cur)

    # Fetch candidates: shipped and barcode present, most recent first
    cur.execute(
        """
        SELECT id, order_id, item_id, barcode, quantity, paid_time, shipped_time,
               rackupdated, removal_cancelled, title
        FROM orders
        WHERE shipped_time IS NOT NULL AND TRIM(shipped_time) <> ''
          AND barcode IS NOT NULL AND TRIM(barcode) <> ''
        ORDER BY shipped_time DESC
        LIMIT 100
        """
    )
    rows = cur.fetchall()

    if not rows:
        print('No shipped orders with barcodes found.')
        return

    now = datetime.datetime.utcnow()
    cutoff = now - datetime.timedelta(hours=LOOKBACK_HOURS)

    recent = []
    for r in rows:
        shipped_dt = parse_iso(r['shipped_time'])
        if not shipped_dt:
            continue
        if shipped_dt >= cutoff:
            recent.append(r)
    
    if not recent:
        print(f'No shipped orders within last {LOOKBACK_HOURS} hours.')
        return

    print(f'Found {len(recent)} shipped orders in last {LOOKBACK_HOURS}h (showing up to {MAX_RESET})')
    for r in recent[:MAX_RESET]:
        print(f" - {r['order_id']} | barcode={r['barcode']} | shipped={r['shipped_time']} | rackupdated={r['rackupdated']} | removal_cancelled={r['removal_cancelled']}")

    # Reset state
    targeted_ids = [r['id'] for r in recent[:MAX_RESET]]
    if not targeted_ids:
        print('Nothing to reset.')
        return

    q_marks = ','.join(['?']*len(targeted_ids))
    cur.execute(f"UPDATE orders SET rackupdated = 0, removal_cancelled = 0 WHERE id IN ({q_marks})", targeted_ids)

    if FORCE_WITHIN_GRACE:
        # Set shipped_time to 24h ago for demo effect
        demo_time = (now - datetime.timedelta(hours=24)).replace(microsecond=0).isoformat() + 'Z'
        cur.execute(f"UPDATE orders SET shipped_time = ? WHERE id IN ({q_marks})", [demo_time] + targeted_ids)
        print(f"Also set shipped_time to {demo_time} for demo (FORCE_WITHIN_GRACE=True)")

    conn.commit()
    conn.close()
    print('Reset complete. You can now refresh /searchrack (Sold) to see pending removals.')


if __name__ == '__main__':
    main()
