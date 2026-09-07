"""
Rotating SQLite backup utility for Sweet Shelves.

Designed for cron on Linux/Debian:
- makes a consistent SQLite snapshot using the sqlite3 backup API,
- compresses backups with gzip to save space,
- rotates old backups by count,
- can back up one or more databases.

Examples:
  python rotating_backup.py backup
  python rotating_backup.py backup --db searchRack.db --keep 14 --backup-dir /media/dk/USB/sweetshelves-db-backups
  python rotating_backup.py backup --db searchRack.db --db sold.db
  python rotating_backup.py list
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_BACKUP_DIR = BASE_DIR / "backups"
DEFAULT_DATABASES = ["searchRack.db"]
DEFAULT_KEEP = 10


@dataclass
class BackupResult:
    db_name: str
    output_path: Path
    row_count: int | None
    size_bytes: int


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    units = ["B", "KB", "MB", "GB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{num_bytes} B"


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def resolve_databases(names: list[str]) -> list[Path]:
    resolved = []
    for name in names:
        db_path = Path(name)
        if not db_path.is_absolute():
            db_path = BASE_DIR / db_path
        resolved.append(db_path)
    return resolved


def sqlite_row_count(db_path: Path) -> int | None:
    try:
        with closing(sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND lower(name)='searchrack' LIMIT 1")
            if cur.fetchone():
                cur.execute("SELECT COUNT(*) FROM SEARCHRACK")
                return int(cur.fetchone()[0])
    except Exception:
        return None
    return None


def prune_old_backups(backup_dir: Path, db_stem: str, keep: int) -> list[Path]:
    if keep < 1:
        raise ValueError('Keep must be at least 1')
    pattern = f"{db_stem}-*.sqlite3.gz"
    backups = sorted(
        backup_dir.glob(pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = []
    for old_path in backups[keep:]:
        old_path.unlink(missing_ok=True)
        removed.append(old_path)
    return removed


def create_sqlite_backup(db_path: Path, backup_dir: Path) -> BackupResult:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    ensure_directory(backup_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    db_stem = db_path.stem
    output_path = backup_dir / f"{db_stem}-{timestamp}.sqlite3.gz"

    temp_fd, temp_name = tempfile.mkstemp(prefix=f"{db_stem}-", suffix=".sqlite3", dir=str(backup_dir))
    os.close(temp_fd)
    temp_path = Path(temp_name)
    compressed_path = temp_path.with_suffix('.gz.tmp')

    try:
        with closing(sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)) as src_conn, closing(sqlite3.connect(temp_path)) as dst_conn:
            src_conn.backup(dst_conn)
            # A WAL source transfers its journal-mode flag to the snapshot.
            # Make the backup self-contained before compressing or counting it.
            dst_conn.execute('PRAGMA journal_mode=DELETE')

        with open(temp_path, "rb") as src_file, gzip.open(compressed_path, "wb", compresslevel=6) as gz_file:
            shutil.copyfileobj(src_file, gz_file)

        row_count = sqlite_row_count(temp_path)
        os.replace(compressed_path, output_path)
        size_bytes = output_path.stat().st_size
        return BackupResult(
            db_name=db_path.name,
            output_path=output_path,
            row_count=row_count,
            size_bytes=size_bytes,
        )
    finally:
        temp_path.unlink(missing_ok=True)
        compressed_path.unlink(missing_ok=True)


def list_backups(backup_dir: Path, db_filters: list[str] | None = None) -> int:
    if not backup_dir.exists():
        print(f"No backup directory found at {backup_dir}")
        return 0

    all_files = sorted(
        backup_dir.glob("*.sqlite3.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if db_filters:
        allowed = {Path(name).stem for name in db_filters}
        all_files = [p for p in all_files if p.name.rsplit("-", 1)[0] in allowed]

    if not all_files:
        print(f"No backups found in {backup_dir}")
        return 0

    print(f"Backups in {backup_dir}:")
    for path in all_files:
        stat = path.stat()
        created = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  {path.name}")
        print(f"    Created: {created}")
        print(f"    Size:    {format_size(stat.st_size)}")
    return 0


def run_backup(db_paths: list[Path], backup_dir: Path, keep: int) -> int:
    ensure_directory(backup_dir)
    total_bytes = 0

    for db_path in db_paths:
        result = create_sqlite_backup(db_path, backup_dir)
        removed = prune_old_backups(backup_dir, db_path.stem, keep)
        total_bytes += result.size_bytes

        print(f"Created backup for {result.db_name}")
        print(f"  File: {result.output_path}")
        if result.row_count is not None:
            print(f"  Rows: {result.row_count}")
        print(f"  Size: {format_size(result.size_bytes)}")
        if removed:
            print("  Rotated:")
            for removed_path in removed:
                print(f"    {removed_path.name}")

    backup_files = list(backup_dir.glob("*.sqlite3.gz"))
    total_on_disk = sum(path.stat().st_size for path in backup_files)
    print(f"New backup data written: {format_size(total_bytes)}")
    print(f"Total compressed backup usage in {backup_dir}: {format_size(total_on_disk)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rotating gzip-compressed SQLite backups")
    subparsers = parser.add_subparsers(dest="command")

    backup_parser = subparsers.add_parser("backup", help="Create one or more backups")
    backup_parser.add_argument(
        "--db",
        action="append",
        dest="databases",
        default=None,
        help="Database filename or absolute path. Can be provided multiple times.",
    )
    backup_parser.add_argument(
        "--backup-dir",
        default=str(DEFAULT_BACKUP_DIR),
        help="Directory to store compressed backups",
    )
    backup_parser.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_KEEP,
        help="How many backups to keep per database",
    )

    list_parser = subparsers.add_parser("list", help="List backups")
    list_parser.add_argument(
        "--backup-dir",
        default=str(DEFAULT_BACKUP_DIR),
        help="Directory containing backups",
    )
    list_parser.add_argument(
        "--db",
        action="append",
        dest="databases",
        default=None,
        help="Optional database filename filter. Can be provided multiple times.",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    command = args.command or "backup"
    databases = args.databases or DEFAULT_DATABASES
    backup_dir = Path(args.backup_dir).expanduser()
    keep = getattr(args, "keep", DEFAULT_KEEP)

    if command == "backup":
        if keep < 1:
            print("--keep must be at least 1")
            return 1
        db_paths = resolve_databases(databases)
        return run_backup(db_paths, backup_dir, keep)

    if command == "list":
        return list_backups(backup_dir, databases)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
