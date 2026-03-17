from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
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


def _parse_dt(value: object) -> datetime | None:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except Exception:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _iter_blank_lot_special_rows(cur: sqlite3.Cursor, updated_date: str = '') -> list[sqlite3.Row]:
    where_parts = [
        "LOWER(TRIM(COALESCE(s.status, ''))) IN ('bad', 'return')",
        "INSTR(COALESCE(s.upc, ''), '-') > 0",
        "COALESCE(s.lot_number, '') = ''",
    ]
    params: list[object] = []
    normalized_date = str(updated_date or '').strip()
    if normalized_date:
        where_parts.append("date(COALESCE(s.updated_at, '')) = ?")
        params.append(normalized_date)
    cur.execute(
        f'''
        SELECT s.id, s.upc, COALESCE(s.lot_number, '') AS lot_number,
               LOWER(TRIM(COALESCE(s.status, ''))) AS status,
               COALESCE(s.reason, '') AS reason,
               COALESCE(s.note, '') AS note,
               COALESCE(s.quantity, 1) AS quantity,
               COALESCE(s.updated_at, '') AS updated_at
        FROM items_prep_status s
        WHERE {' AND '.join(where_parts)}
        ORDER BY datetime(COALESCE(s.updated_at, '')) DESC, s.id DESC
        ''',
        tuple(params),
    )
    return cur.fetchall()


def _load_base_rows(cur: sqlite3.Cursor, base_upc: str) -> list[sqlite3.Row]:
    cur.execute(
        '''
        SELECT id, upc, COALESCE(item_description, '') AS item_description,
               COALESCE(image_url, '') AS image_url,
               COALESCE(lot_number, '') AS lot_number,
               COALESCE(bol_number, '') AS bol_number,
               COALESCE(import_date, '') AS import_date
        FROM bol_items
        WHERE upc = ? COLLATE NOCASE
        ORDER BY id DESC
        ''',
        (base_upc,),
    )
    return cur.fetchall()


def _pick_base_row(base_rows: list[sqlite3.Row], occurred_at: str) -> sqlite3.Row | None:
    candidates = [row for row in base_rows if _normalize_lot(row['lot_number'])]
    if not candidates:
        return None

    occurred_dt = _parse_dt(occurred_at)

    def _sort_key(row: sqlite3.Row) -> tuple[datetime, int]:
        import_dt = _parse_dt(row['import_date']) or datetime.min
        return import_dt, int(row['id'] or 0)

    if occurred_dt is not None:
        eligible = []
        for row in candidates:
            import_dt = _parse_dt(row['import_date'])
            if import_dt is not None and import_dt <= occurred_dt:
                eligible.append(row)
        if eligible:
            candidates = eligible

    candidates.sort(key=_sort_key, reverse=True)
    return candidates[0]


def _select_blank_special_bol_row(cur: sqlite3.Cursor, special_upc: str) -> sqlite3.Row | None:
    cur.execute(
        '''
        SELECT id, COALESCE(item_description, '') AS item_description,
               COALESCE(image_url, '') AS image_url,
               COALESCE(lot_number, '') AS lot_number,
               COALESCE(bol_number, '') AS bol_number,
               COALESCE(import_date, '') AS import_date,
               COALESCE(original_qty, 0) AS original_qty,
               COALESCE(unchecked_qty, 0) AS unchecked_qty,
               COALESCE(good_qty, 0) AS good_qty,
               COALESCE(bad_qty, 0) AS bad_qty,
               COALESCE(quantity, 0) AS quantity,
               COALESCE(temporary, 0) AS temporary
        FROM bol_items
        WHERE upc = ? COLLATE NOCASE
          AND COALESCE(lot_number, '') = ''
        ORDER BY datetime(COALESCE(import_date, '')) DESC, id DESC
        LIMIT 1
        ''',
        (special_upc,),
    )
    return cur.fetchone()


def repair_database(db_path: Path, dry_run: bool, updated_date: str = '') -> int:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    target_rows = _iter_blank_lot_special_rows(cur, updated_date=updated_date)
    scope_text = f' for {updated_date}' if str(updated_date or '').strip() else ''
    print(f'Found {len(target_rows)} blank-lot special-status rows in {db_path.name}{scope_text}')

    repaired = 0
    skipped = 0
    updated_bol = 0
    created_bol = 0

    try:
        for row in target_rows:
            special_upc = str(row['upc'] or '').strip()
            status = _normalize_status(row['status'])
            qty = max(1, _coerce_int(row['quantity'], 1))
            updated_at = str(row['updated_at'] or '').strip()
            base_upc = special_upc.split('-', 1)[0].strip()

            base_row = _pick_base_row(_load_base_rows(cur, base_upc), updated_at)
            if not base_row:
                skipped += 1
                print(f'SKIP no base row with lot for {special_upc}')
                continue

            inferred_lot = _normalize_lot(base_row['lot_number'])
            if not inferred_lot:
                skipped += 1
                print(f'SKIP could not infer lot for {special_upc}')
                continue

            existing_special = _select_blank_special_bol_row(cur, special_upc)
            print(
                f'REPAIR {special_upc} status={status} qty={qty} '
                f'lot=(blank)->{inferred_lot}'
            )

            if existing_special:
                updated_bol += 1
            else:
                created_bol += 1

            if dry_run:
                repaired += 1
                continue

            cur.execute(
                '''
                UPDATE items_prep_status
                SET lot_number = ?
                WHERE id = ?
                ''',
                (inferred_lot, int(row['id'])),
            )

            if existing_special:
                row_qty = max(
                    qty,
                    _coerce_int(existing_special['quantity'], 0),
                    _coerce_int(existing_special['original_qty'], 0),
                    _coerce_int(existing_special['good_qty'], 0),
                    _coerce_int(existing_special['bad_qty'], 0),
                    1,
                )
                good_qty = max(
                    _coerce_int(existing_special['good_qty'], 0),
                    qty if status == 'return' else 0,
                )
                bad_qty = max(
                    _coerce_int(existing_special['bad_qty'], 0),
                    qty if status == 'bad' else 0,
                )
                import_date = (
                    str(existing_special['import_date'] or '').strip()
                    or updated_at
                    or str(base_row['import_date'] or '').strip()
                )
                bol_number = (
                    str(existing_special['bol_number'] or '').strip()
                    or str(base_row['bol_number'] or '').strip()
                    or inferred_lot
                )
                item_description = (
                    str(existing_special['item_description'] or '').strip()
                    or str(base_row['item_description'] or '').strip()
                )
                image_url = (
                    str(existing_special['image_url'] or '').strip()
                    or str(base_row['image_url'] or '').strip()
                )
                cur.execute(
                    '''
                    UPDATE bol_items
                    SET item_description = ?,
                        image_url = ?,
                        lot_number = ?,
                        bol_number = ?,
                        import_date = ?,
                        original_qty = ?,
                        unchecked_qty = 0,
                        good_qty = ?,
                        bad_qty = ?,
                        quantity = ?
                    WHERE id = ?
                    ''',
                    (
                        item_description,
                        image_url,
                        inferred_lot,
                        bol_number,
                        import_date,
                        row_qty,
                        good_qty,
                        bad_qty,
                        row_qty,
                        int(existing_special['id']),
                    ),
                )
            else:
                import_date = updated_at or str(base_row['import_date'] or '').strip()
                bol_number = str(base_row['bol_number'] or '').strip() or inferred_lot
                item_description = str(base_row['item_description'] or '').strip()
                image_url = str(base_row['image_url'] or '').strip()
                good_qty = qty if status == 'return' else 0
                bad_qty = qty if status == 'bad' else 0
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
                        item_description,
                        image_url,
                        inferred_lot,
                        bol_number,
                        import_date,
                        qty,
                        good_qty,
                        bad_qty,
                        qty,
                    ),
                )
            repaired += 1

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()

    print(f'Repaired: {repaired}')
    print(f'Updated bol rows: {updated_bol}')
    print(f'Created bol rows: {created_bol}')
    print(f'Skipped: {skipped}')
    if dry_run:
        print('Dry run only. No changes were written.')
    return repaired


def main() -> int:
    parser = argparse.ArgumentParser(description='Repair blank-lot BAD/RETURN suffix rows in bol.db.')
    parser.add_argument('db_path', nargs='?', default='bol.db', help='Path to bol.db')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without writing them')
    parser.add_argument('--updated-date', default='', help='Only repair rows whose items_prep_status updated_at date matches YYYY-MM-DD')
    args = parser.parse_args()

    db_path = Path(args.db_path).expanduser().resolve()
    if not db_path.exists():
        print(f'Database not found: {db_path}')
        return 1

    repair_database(db_path, dry_run=args.dry_run, updated_date=args.updated_date)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
