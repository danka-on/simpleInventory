from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def _normalize_status(value: object) -> str:
    return str(value or '').strip().lower()


def _normalize_lot(value: object) -> str:
    return str(value or '').strip()


def _coerce_int(value: object, fallback: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return fallback


def _select_base_row(cur: sqlite3.Cursor, base_upc: str, lot_number: str) -> sqlite3.Row | None:
    if lot_number:
        cur.execute(
            '''
            SELECT id, upc, item_description, image_url, COALESCE(lot_number, '') AS lot_number,
                   bol_number, import_date, COALESCE(original_qty, 0) AS original_qty,
                   COALESCE(good_qty, 0) AS good_qty, COALESCE(bad_qty, 0) AS bad_qty,
                   unchecked_qty, COALESCE(quantity, 0) AS quantity
            FROM bol_items
            WHERE upc = ? COLLATE NOCASE
              AND COALESCE(lot_number, '') = ? COLLATE NOCASE
            ORDER BY import_date DESC, id DESC
            LIMIT 1
            ''',
            (base_upc, lot_number),
        )
        row = cur.fetchone()
        if row:
            return row

    cur.execute(
        '''
        SELECT id, upc, item_description, image_url, COALESCE(lot_number, '') AS lot_number,
               bol_number, import_date, COALESCE(original_qty, 0) AS original_qty,
               COALESCE(good_qty, 0) AS good_qty, COALESCE(bad_qty, 0) AS bad_qty,
               unchecked_qty, COALESCE(quantity, 0) AS quantity
        FROM bol_items
        WHERE upc = ? COLLATE NOCASE
        ORDER BY import_date DESC, id DESC
        LIMIT 1
        ''',
        (base_upc,),
    )
    return cur.fetchone()


def _iter_orphan_rows(cur: sqlite3.Cursor) -> list[sqlite3.Row]:
    cur.execute(
        '''
        SELECT s.id, s.upc, COALESCE(s.lot_number, '') AS lot_number,
               LOWER(TRIM(COALESCE(s.status, ''))) AS status,
               COALESCE(s.reason, '') AS reason,
               COALESCE(s.note, '') AS note,
               COALESCE(s.quantity, 1) AS quantity,
               COALESCE(s.updated_at, '') AS updated_at
        FROM items_prep_status s
        LEFT JOIN bol_items b
          ON b.upc = s.upc
         AND COALESCE(b.lot_number, '') = COALESCE(s.lot_number, '')
        WHERE LOWER(TRIM(COALESCE(s.status, ''))) IN ('bad', 'return')
          AND INSTR(COALESCE(s.upc, ''), '-') > 0
          AND b.id IS NULL
        ORDER BY datetime(COALESCE(s.updated_at, '')) DESC, s.id DESC
        '''
    )
    return cur.fetchall()


def repair_database(db_path: Path, dry_run: bool) -> int:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    orphan_rows = _iter_orphan_rows(cur)
    print(f'Found {len(orphan_rows)} orphan special-status rows in {db_path.name}')

    repaired = 0
    skipped = 0

    try:
        for row in orphan_rows:
            special_upc = str(row['upc'] or '').strip()
            lot_number = _normalize_lot(row['lot_number'])
            status = _normalize_status(row['status'])
            qty = max(1, _coerce_int(row['quantity'], 1))
            updated_at = str(row['updated_at'] or '').strip()
            base_upc = special_upc.split('-', 1)[0].strip()

            base_row = _select_base_row(cur, base_upc, lot_number)
            if not base_row:
                skipped += 1
                print(f'SKIP no base row for {special_upc} lot={lot_number or "(none)"}')
                continue

            base_id = int(base_row['id'])
            base_desc = str(base_row['item_description'] or '')
            base_img = str(base_row['image_url'] or '')
            base_bol = base_row['bol_number']
            base_import_date = str(base_row['import_date'] or '')
            base_good = _coerce_int(base_row['good_qty'], 0)
            base_bad = _coerce_int(base_row['bad_qty'], 0)
            base_original = _coerce_int(base_row['original_qty'], 0)
            base_unchecked_raw = base_row['unchecked_qty']
            if base_unchecked_raw is None:
                base_unchecked = max(0, base_original - base_good - base_bad)
            else:
                base_unchecked = max(0, _coerce_int(base_unchecked_raw, 0))

            move_from_good = bool(base_good > 0 and (base_good >= qty or base_unchecked <= 0))
            remaining_good = max(0, base_good - qty) if move_from_good else base_good
            remaining_unchecked = base_unchecked if move_from_good else max(0, base_unchecked - qty)
            new_bad_total = base_bad + qty if status == 'bad' else base_bad

            special_good_qty = qty if status == 'return' else 0
            special_bad_qty = qty if status == 'bad' else 0
            special_import_date = updated_at or base_import_date

            print(
                f'REPAIR {special_upc} lot={lot_number or "(none)"} '
                f'status={status} qty={qty} source={"good" if move_from_good else "unchecked"}'
            )

            if dry_run:
                repaired += 1
                continue

            cur.execute(
                '''
                INSERT INTO bol_items (
                    upc, item_description, image_url, lot_number, bol_number, import_date,
                    temporary, original_qty, unchecked_qty, good_qty, bad_qty, quantity
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, 0, ?, ?, ?)
                ''',
                (
                    special_upc,
                    base_desc,
                    base_img,
                    lot_number,
                    base_bol,
                    special_import_date,
                    qty,
                    special_good_qty,
                    special_bad_qty,
                    qty,
                ),
            )

            cur.execute(
                '''
                UPDATE bol_items
                SET good_qty = ?, bad_qty = ?, unchecked_qty = ?
                WHERE id = ?
                ''',
                (remaining_good, new_bad_total, remaining_unchecked, base_id),
            )

            repaired += 1

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()

    print(f'Repaired: {repaired}')
    print(f'Skipped: {skipped}')
    if dry_run:
        print('Dry run only. No changes were written.')
    return repaired


def main() -> int:
    parser = argparse.ArgumentParser(description='Repair orphan BAD/RETURN items-to-list rows in bol.db.')
    parser.add_argument('db_path', nargs='?', default='bol.db', help='Path to bol.db')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without writing them')
    args = parser.parse_args()

    db_path = Path(args.db_path).expanduser().resolve()
    if not db_path.exists():
        print(f'Database not found: {db_path}')
        return 1

    repair_database(db_path, dry_run=args.dry_run)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
