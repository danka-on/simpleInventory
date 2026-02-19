#!/usr/bin/env python3
"""
Reset manual marketplace flags for the latest (or specified) BOL lot.

Manual items are detected from:
1) listinglog.db listing_log rows where source='user' and meta_json lot matches.
2) bol.db rows currently marked with listed_*_source='user' in the target lot.

By default this runs in dry-run mode.
Use --apply to execute updates.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

PLATFORMS = ("amazon", "ebay", "facebook")


def _norm(s: object) -> str:
    return str(s or "").strip().lower()


def _find_latest_lot(cur: sqlite3.Cursor) -> str:
    cur.execute(
        """
        SELECT lot_number, MAX(import_date) AS latest_import_date
        FROM bol_items
        WHERE TRIM(COALESCE(lot_number, '')) <> ''
        GROUP BY lot_number
        ORDER BY latest_import_date DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    return str(row[0]).strip() if row and row[0] is not None else ""


def _load_manual_history_pairs(listinglog_db: Path, lot_number: str) -> Set[Tuple[str, str]]:
    out: Set[Tuple[str, str]] = set()
    if not listinglog_db.exists():
        return out

    conn = sqlite3.connect(str(listinglog_db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT upc, platform, source, meta_json
        FROM listing_log
        WHERE LOWER(COALESCE(source, '')) = 'user'
          AND LOWER(COALESCE(platform, '')) IN ('amazon', 'ebay', 'facebook')
        """
    )
    for row in cur.fetchall():
        meta_json = row["meta_json"]
        if not meta_json:
            continue
        lot = ""
        try:
            meta = json.loads(meta_json)
            lot = str(meta.get("lot_number") or "").strip()
        except Exception:
            lot = ""
        if lot == lot_number:
            out.add((_norm(row["upc"]), _norm(row["platform"])))
    conn.close()
    return out


def _load_bol_rows_for_lot(cur: sqlite3.Cursor, lot_number: str) -> List[sqlite3.Row]:
    cur.execute(
        """
        SELECT
            id,
            upc,
            lot_number,
            COALESCE(listed_amazon, 0) AS listed_amazon,
            listed_amazon_date,
            COALESCE(listed_amazon_source, '') AS listed_amazon_source,
            COALESCE(listed_ebay, 0) AS listed_ebay,
            listed_ebay_date,
            COALESCE(listed_ebay_source, '') AS listed_ebay_source,
            COALESCE(listed_facebook, 0) AS listed_facebook,
            listed_facebook_date,
            COALESCE(listed_facebook_source, '') AS listed_facebook_source
        FROM bol_items
        WHERE COALESCE(lot_number, '') = ?
        ORDER BY id ASC
        """,
        (lot_number,),
    )
    return cur.fetchall()


def _collect_targets(
    rows: Iterable[sqlite3.Row],
    manual_history_pairs: Set[Tuple[str, str]],
) -> Dict[int, Set[str]]:
    targets: Dict[int, Set[str]] = {}
    for row in rows:
        row_id = int(row["id"])
        upc_key = _norm(row["upc"])
        for platform in PLATFORMS:
            listed = int(row[f"listed_{platform}"] or 0)
            source = _norm(row[f"listed_{platform}_source"])
            has_date = bool(str(row[f"listed_{platform}_date"] or "").strip())

            manual_historical = (upc_key, platform) in manual_history_pairs
            manual_current = source == "user"
            if not (manual_historical or manual_current):
                continue

            # Only schedule rows that currently carry state to clear.
            if listed == 1 or source == "user" or has_date:
                targets.setdefault(row_id, set()).add(platform)
    return targets


def _backup_db(path: Path) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.name}.manual_reset_backup_{ts}")
    shutil.copy2(path, backup_path)
    return backup_path


def _apply_reset(conn: sqlite3.Connection, targets: Dict[int, Set[str]]) -> None:
    cur = conn.cursor()
    for row_id, platforms in targets.items():
        if "amazon" in platforms:
            cur.execute(
                """
                UPDATE bol_items
                SET listed_amazon = 0,
                    listed_amazon_date = NULL,
                    listed_amazon_source = NULL
                WHERE id = ?
                """,
                (row_id,),
            )
        if "ebay" in platforms:
            cur.execute(
                """
                UPDATE bol_items
                SET listed_ebay = 0,
                    listed_ebay_date = NULL,
                    listed_ebay_source = NULL
                WHERE id = ?
                """,
                (row_id,),
            )
        if "facebook" in platforms:
            cur.execute(
                """
                UPDATE bol_items
                SET listed_facebook = 0,
                    listed_facebook_date = NULL,
                    listed_facebook_qty = NULL,
                    listed_facebook_source = NULL
                WHERE id = ?
                """,
                (row_id,),
            )

    # Recompute legacy list_status for changed rows only.
    for row_id in targets.keys():
        cur.execute(
            """
            UPDATE bol_items
            SET list_status = CASE
                WHEN COALESCE(listed_amazon, 0) = 1
                  OR COALESCE(listed_ebay, 0) = 1
                  OR COALESCE(listed_facebook, 0) = 1
                THEN 'listed'
                ELSE NULL
            END
            WHERE id = ?
            """,
            (row_id,),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset manual marketplace flags for latest lot.")
    parser.add_argument("--app-dir", default=".", help="Directory containing bol.db/listinglog.db")
    parser.add_argument("--lot", default="", help="Override lot number (default: auto-detect latest lot)")
    parser.add_argument("--apply", action="store_true", help="Execute updates (default is dry-run)")
    parser.add_argument("--show", type=int, default=40, help="Max rows to print in preview")
    args = parser.parse_args()

    app_dir = Path(args.app_dir).resolve()
    bol_db = app_dir / "bol.db"
    listinglog_db = app_dir / "listinglog.db"

    if not bol_db.exists():
        print(f"ERROR: missing {bol_db}")
        return 1

    conn = sqlite3.connect(str(bol_db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    lot_number = str(args.lot or "").strip() or _find_latest_lot(cur)
    if not lot_number:
        print("ERROR: could not determine latest lot")
        conn.close()
        return 1

    rows = _load_bol_rows_for_lot(cur, lot_number)
    manual_history_pairs = _load_manual_history_pairs(listinglog_db, lot_number)
    targets = _collect_targets(rows, manual_history_pairs)

    per_platform = {p: 0 for p in PLATFORMS}
    for platforms in targets.values():
        for p in platforms:
            per_platform[p] += 1

    print(f"Target lot: {lot_number}")
    print(f"Rows in lot: {len(rows)}")
    print(f"Manual history UPC+platform pairs from listinglog: {len(manual_history_pairs)}")
    print(f"Rows to reset: {len(targets)}")
    print(
        "Platform resets: "
        f"amazon={per_platform['amazon']}, "
        f"ebay={per_platform['ebay']}, "
        f"facebook={per_platform['facebook']}"
    )

    if targets:
        id_to_row = {int(r["id"]): r for r in rows}
        print("\nPreview:")
        shown = 0
        for row_id in sorted(targets.keys()):
            if shown >= max(0, args.show):
                break
            row = id_to_row[row_id]
            platforms = ",".join(sorted(targets[row_id]))
            print(f"  id={row_id} upc={row['upc']} platforms={platforms}")
            shown += 1
        if len(targets) > shown:
            print(f"  ... {len(targets) - shown} more")

    if not args.apply:
        print("\nDry-run only. Re-run with --apply to execute.")
        conn.close()
        return 0

    if not targets:
        print("\nNothing to reset.")
        conn.close()
        return 0

    backup_path = _backup_db(bol_db)
    print(f"\nBackup created: {backup_path}")

    try:
        conn.execute("BEGIN")
        _apply_reset(conn, targets)
        conn.commit()
        print("Apply complete.")
    except Exception as e:
        conn.rollback()
        print(f"ERROR: apply failed, rolled back: {e}")
        conn.close()
        return 1

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
