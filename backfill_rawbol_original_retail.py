"""
Retail-only backfill utility for rawbol.db.

Purpose:
- Read original BOL files (.xls/.xlsx/.csv).
- Extract UPC + original retail values.
- Update ONLY rawbol.db.raw_bol_items.original_retail.

Safety:
- Matches rows by LOT + normalized UPC key.
- Does not insert/delete rows.
- Does not change quantity, description, image, or sync tables.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sqlite3
from typing import Dict, List, Optional, Sequence, Set, Tuple

try:
    import pandas as pd
except Exception:  # pragma: no cover - runtime dependency guard
    pd = None


DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rawbol.db")
EXTENSIONS = (".xls", ".xlsx", ".csv")
RETAIL_COLUMN_CANDIDATES = (
    "original retail",
    "original_retail",
    "original retail price",
    "original_retail_price",
    "original retail $",
    "retail price",
    "retail_price",
)


def _safe_text(value) -> str:
    if value is None:
        return ""
    txt = str(value).strip()
    if txt.lower() in ("nan", "none", "null"):
        return ""
    return txt


def _parse_money(value) -> Optional[float]:
    txt = _safe_text(value)
    if not txt:
        return None
    txt = txt.replace("$", "").replace(",", "").strip()
    if txt.startswith("(") and txt.endswith(")"):
        txt = "-" + txt[1:-1]
    try:
        n = float(txt)
    except Exception:
        return None
    if n <= 0:
        return None
    return round(n, 2)


def _normalize_upc_key(value) -> str:
    upc = _safe_text(value)
    if not upc:
        return ""

    if upc.endswith(".0") and upc[:-2].isdigit():
        upc = upc[:-2]

    if "-" in upc:
        base, suffix = upc.split("-", 1)
        base = base.strip()
        suffix = suffix.strip()
    else:
        base, suffix = upc.strip(), ""

    if base.isdigit():
        base = base.lstrip("0") or "0"

    key = f"{base}-{suffix}" if suffix else base
    return key.strip().lower()


def _choose_retail_column(columns: Sequence[str]) -> Optional[str]:
    normalized = {str(c).strip().lower(): c for c in columns}
    for key in RETAIL_COLUMN_CANDIDATES:
        if key in normalized:
            return normalized[key]

    for key, original in normalized.items():
        compact = "".join(ch for ch in key if ch.isalnum())
        if "original" in compact and "retail" in compact:
            return original

    for key, original in normalized.items():
        if "retail" in key and "qty" not in key and "quantity" not in key:
            return original
    return None


def _find_upc_row(df_raw: pd.DataFrame) -> Optional[int]:
    for idx, row in df_raw.iterrows():
        for cell in row.values:
            if _safe_text(cell).upper() == "UPC":
                return int(idx)
    return None


def _extract_lot_from_header(df_raw: pd.DataFrame, upc_row_idx: int) -> str:
    # Preferred: LOCATION header row + LOT column + first data row below.
    location_idx = None
    for idx in range(upc_row_idx):
        first_cell = _safe_text(df_raw.iloc[idx, 0]).upper() if df_raw.shape[1] > 0 else ""
        if first_cell == "LOCATION":
            location_idx = idx
            break

    if location_idx is not None:
        lot_col_idx = None
        row = df_raw.iloc[location_idx]
        for col_idx, cell in enumerate(row.values):
            txt = _safe_text(cell).upper()
            if "LOT" in txt and ("#" in txt or "NUMBER" in txt or txt.endswith("NO") or txt.endswith("NO.")):
                lot_col_idx = col_idx
                break
        if lot_col_idx is not None and (location_idx + 1) < upc_row_idx:
            lot = _safe_text(df_raw.iloc[location_idx + 1, lot_col_idx])
            if lot:
                return lot

    # Fallback: scan header cells for "LOT ... <value>".
    lot_pattern = re.compile(r"LOT\s*(?:#|NO\.?|NUMBER)?\s*[:\-]?\s*([A-Za-z0-9\-_./]+)", re.IGNORECASE)
    for idx in range(upc_row_idx):
        row = df_raw.iloc[idx]
        for cell in row.values:
            txt = _safe_text(cell)
            if not txt:
                continue
            m = lot_pattern.search(txt)
            if m:
                candidate = _safe_text(m.group(1))
                if candidate and candidate.upper() not in ("LOT", "NUMBER", "#"):
                    return candidate

    return ""


def _read_raw_dataframe(file_path: str) -> pd.DataFrame:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv":
        return pd.read_csv(file_path, header=None, dtype=str)
    if ext == ".xls":
        return pd.read_excel(file_path, engine="xlrd", header=None, dtype=str)
    if ext == ".xlsx":
        return pd.read_excel(file_path, engine="openpyxl", header=None, dtype=str)
    raise ValueError(f"Unsupported extension: {ext}")


def _read_items_dataframe(file_path: str, upc_row_idx: int) -> pd.DataFrame:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv":
        df = pd.read_csv(file_path, header=upc_row_idx, dtype=str)
    elif ext == ".xls":
        df = pd.read_excel(file_path, engine="xlrd", header=upc_row_idx, dtype=str)
    elif ext == ".xlsx":
        df = pd.read_excel(file_path, engine="openpyxl", header=upc_row_idx, dtype=str)
    else:
        raise ValueError(f"Unsupported extension: {ext}")

    df.columns = [str(c).strip() for c in df.columns]
    return df


def _extract_retail_rows(
    file_path: str,
    forced_lot: str = "",
) -> Tuple[str, Dict[str, float], str]:
    """
    Returns: (lot_number, {upc_key: retail_value}, error_message)
    """
    try:
        df_raw = _read_raw_dataframe(file_path)
    except Exception as e:
        return "", {}, f"read error: {e}"

    upc_row_idx = _find_upc_row(df_raw)
    if upc_row_idx is None:
        return "", {}, 'could not find "UPC" header row'

    lot_number = _safe_text(forced_lot) or _extract_lot_from_header(df_raw, upc_row_idx)
    if not lot_number:
        return "", {}, "could not extract LOT # (use --lot to force one)"

    try:
        df_items = _read_items_dataframe(file_path, upc_row_idx)
    except Exception as e:
        return lot_number, {}, f"item-table read error: {e}"

    upc_col = None
    for c in df_items.columns:
        if str(c).strip().upper() == "UPC":
            upc_col = c
            break
    if upc_col is None:
        return lot_number, {}, "missing UPC column in item table"

    retail_col = _choose_retail_column(df_items.columns)
    if retail_col is None:
        return lot_number, {}, "missing original retail column"

    results: Dict[str, float] = {}
    for _, row in df_items.iterrows():
        upc_key = _normalize_upc_key(row.get(upc_col))
        if not upc_key:
            continue
        retail_val = _parse_money(row.get(retail_col))
        if retail_val is None:
            continue

        existing = results.get(upc_key)
        if existing is None or existing <= 0:
            results[upc_key] = retail_val

    if not results:
        return lot_number, {}, "no valid UPC + retail rows found"

    return lot_number, results, ""


def _collect_files(inputs: Sequence[str], recursive: bool) -> List[str]:
    files: Set[str] = set()
    for token in inputs:
        token = token.strip()
        if not token:
            continue

        if os.path.isdir(token):
            pattern = "**/*" if recursive else "*"
            for ext in EXTENSIONS:
                for p in glob.glob(os.path.join(token, pattern + ext), recursive=recursive):
                    if os.path.isfile(p):
                        files.add(os.path.abspath(p))
            continue

        if any(ch in token for ch in ("*", "?")):
            for p in glob.glob(token, recursive=recursive):
                if os.path.isfile(p) and os.path.splitext(p)[1].lower() in EXTENSIONS:
                    files.add(os.path.abspath(p))
            continue

        if os.path.isfile(token) and os.path.splitext(token)[1].lower() in EXTENSIONS:
            files.add(os.path.abspath(token))

    return sorted(files)


def _ensure_original_retail_column(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(raw_bol_items)")
    cols = [str(r[1]).lower() for r in cur.fetchall()]
    if "original_retail" not in cols:
        cur.execute("ALTER TABLE raw_bol_items ADD COLUMN original_retail REAL")
        conn.commit()


def _build_lot_row_map(conn: sqlite3.Connection, lot_number: str) -> Dict[str, List[Tuple[int, float]]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, upc, COALESCE(original_retail, 0)
        FROM raw_bol_items
        WHERE lot_number = ?
        """,
        (lot_number,),
    )

    out: Dict[str, List[Tuple[int, float]]] = {}
    for row_id, upc, current_retail in cur.fetchall():
        key = _normalize_upc_key(upc)
        if not key:
            continue
        bucket = out.get(key)
        if bucket is None:
            bucket = []
            out[key] = bucket
        try:
            current = float(current_retail or 0)
        except Exception:
            current = 0.0
        bucket.append((int(row_id), round(current, 2)))
    return out


def main() -> int:
    if pd is None:
        print("Missing dependency: pandas")
        print("Install with: pip install pandas openpyxl xlrd")
        return 1

    parser = argparse.ArgumentParser(
        description="Backfill rawbol.db.raw_bol_items.original_retail from original BOL files."
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="Files, directories, or glob patterns (.xls/.xlsx/.csv).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without writing to the database.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite non-zero original_retail values (default only fills empty/zero).",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively scan directories and recursive globs.",
    )
    parser.add_argument(
        "--lot",
        default="",
        help="Force this LOT # for all files (use only if header extraction fails).",
    )
    args = parser.parse_args()

    files = _collect_files(args.inputs, recursive=args.recursive)
    if not files:
        print("No matching files found.")
        return 1

    conn = sqlite3.connect(DB_PATH)
    try:
        _ensure_original_retail_column(conn)
        cur = conn.cursor()

        total_files = 0
        total_lot_missing = 0
        total_rows_seen = 0
        total_upc_missing = 0
        total_skipped_existing = 0
        total_updated = 0

        for file_path in files:
            total_files += 1
            lot, retail_map, err = _extract_retail_rows(file_path, forced_lot=args.lot)
            rel_name = os.path.basename(file_path)

            if err:
                print(f"[SKIP] {rel_name}: {err}")
                continue

            lot_rows = _build_lot_row_map(conn, lot)
            if not lot_rows:
                total_lot_missing += 1
                print(f"[SKIP] {rel_name}: lot '{lot}' not found in raw_bol_items")
                continue

            file_updates = 0
            file_missing = 0
            file_skipped_existing = 0

            for upc_key, retail_val in retail_map.items():
                total_rows_seen += 1
                row_matches = lot_rows.get(upc_key)
                if not row_matches:
                    file_missing += 1
                    total_upc_missing += 1
                    continue

                for row_id, current_val in row_matches:
                    if (not args.overwrite) and current_val > 0:
                        file_skipped_existing += 1
                        total_skipped_existing += 1
                        continue
                    if args.dry_run:
                        file_updates += 1
                        total_updated += 1
                        continue
                    cur.execute(
                        "UPDATE raw_bol_items SET original_retail = ? WHERE id = ?",
                        (retail_val, row_id),
                    )
                    file_updates += 1
                    total_updated += 1

            status = "DRY-RUN" if args.dry_run else "UPDATED"
            print(
                f"[{status}] {rel_name} | lot={lot} | source_upcs={len(retail_map)} | "
                f"updated={file_updates} | missing_upc={file_missing} | skipped_existing={file_skipped_existing}"
            )

        if not args.dry_run:
            conn.commit()

        print("")
        print("Summary")
        print(f"- files_processed: {total_files}")
        print(f"- file_lot_not_found: {total_lot_missing}")
        print(f"- source_upc_rows_seen: {total_rows_seen}")
        print(f"- updated_rows: {total_updated}")
        print(f"- missing_upc_matches: {total_upc_missing}")
        print(f"- skipped_existing_nonzero: {total_skipped_existing}")
        print(f"- mode: {'dry-run' if args.dry_run else 'write'}")

    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
