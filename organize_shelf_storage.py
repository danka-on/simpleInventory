from __future__ import annotations

import argparse
import hashlib
import re
import shutil
from pathlib import Path

from PIL import Image


SHELF_PATTERNS = {
    "office": re.compile(r"^(?:or\d+s\d+(?:b\d+)?|omr\d+s\d+(?:b\d+)?|ofloor\d+(?:b\d+)?)\.png$", re.IGNORECASE),
    "garage": re.compile(r"^(?:gr\d+s\d+(?:b\d+)?|gmid\d+(?:b\d+)?|gfloor\d+(?:b\d+)?|misc\d+(?:b\d+)?)\.png$", re.IGNORECASE),
    "hallway": re.compile(r"^(?:h(?:[1-9]|1[0-4])|ho[1-3])\.png$", re.IGNORECASE),
    "misc": re.compile(r"^mb\d+\.png$", re.IGNORECASE),
}

MAP_PATTERNS = {
    "office": re.compile(r"^officemap(?:_.+)?\.(?:png|jpg|jpeg)$", re.IGNORECASE),
    "garage": re.compile(r"^garagemap(?:_.+)?\.(?:png|jpg|jpeg)$", re.IGNORECASE),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_shelf_file(name: str) -> str | None:
    for area, pattern in SHELF_PATTERNS.items():
        if pattern.fullmatch(name):
            return area
    return None


def classify_map_file(name: str) -> str | None:
    for area, pattern in MAP_PATTERNS.items():
        if pattern.fullmatch(name):
            return area
    return None


def ensure_area_layout(shelves_root: Path) -> None:
    for area in SHELF_PATTERNS:
        (shelves_root / area / "maps").mkdir(parents=True, exist_ok=True)
        (shelves_root / area / "originals").mkdir(parents=True, exist_ok=True)
        (shelves_root / area / "shelf_pics").mkdir(parents=True, exist_ok=True)


def move_or_dedupe(source: Path, destination: Path, dry_run: bool) -> tuple[int, int, int]:
    if source.resolve() == destination.resolve():
        return 0, 0, 0
    if destination.exists():
        if sha256(source) == sha256(destination):
            print(f"DEDUP  {source} -> {destination}")
            if not dry_run:
                source.unlink()
            return 0, 1, 0
        print(f"KEEP   {source} (conflicts with existing {destination})")
        return 0, 0, 1
    print(f"MOVE   {source} -> {destination}")
    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
    return 1, 0, 0


def delete_source(path: Path, dry_run: bool) -> int:
    print(f"DELETE {path}")
    if not dry_run and path.exists():
        path.unlink()
    return 1


def prune_empty_legacy_dirs(shelves_root: Path, dry_run: bool) -> int:
    candidates: list[Path] = []
    for area in SHELF_PATTERNS:
        candidates.append(shelves_root / area / "originals" / "bases")
        candidates.append(shelves_root / "maps" / area / "bases")
        candidates.append(shelves_root / "maps" / area)
        candidates.append(shelves_root / "marked" / area)
    candidates.extend(
        [
            shelves_root / "maps",
            shelves_root / "marked",
            shelves_root / "originals" / "office_map_bases",
            shelves_root / "originals" / "garage_map_bases",
            shelves_root / "originals",
        ]
    )

    removed = 0
    seen: set[Path] = set()
    for directory in sorted(candidates, key=lambda value: len(value.parts), reverse=True):
        if directory in seen:
            continue
        seen.add(directory)
        if not directory.exists() or not directory.is_dir():
            continue
        try:
            next(directory.iterdir())
            continue
        except StopIteration:
            print(f"RMDIR  {directory}")
            if not dry_run:
                directory.rmdir()
            removed += 1
    return removed


def preview_base_candidates(shelves_root: Path, area: str, stem: str) -> list[Path]:
    variants: list[str] = []
    for value in (stem, stem.lower(), stem.upper()):
        if value and value not in variants:
            variants.append(value)
    compact = re.sub(r"b\d+$", "", stem, flags=re.IGNORECASE)
    for value in (compact, compact.lower(), compact.upper()):
        if value and value not in variants:
            variants.append(value)

    candidates: list[Path] = []
    for value in variants:
        candidates.append(shelves_root / area / "shelf_pics" / f"{value}.png")
        candidates.append(shelves_root / area / "originals" / f"{value}.png")

    legacy_maps_bases = shelves_root / "maps" / area / "bases"
    for value in variants:
        candidates.append(legacy_maps_bases / f"{value}.png")

    legacy_originals = shelves_root / "originals"
    if area == "office":
        legacy_named_bases = legacy_originals / "office_map_bases"
    elif area == "garage":
        legacy_named_bases = legacy_originals / "garage_map_bases"
    else:
        legacy_named_bases = None

    if legacy_named_bases is not None:
        for value in variants:
            candidates.append(legacy_named_bases / f"{value}.png")

    return candidates


def looks_like_combo_image(path: Path, shelves_root: Path, area: str) -> bool:
    if area not in ("office", "garage"):
        return False
    stem = path.stem
    base_path = next((candidate for candidate in preview_base_candidates(shelves_root, area, stem) if candidate.exists() and candidate.resolve() != path.resolve()), None)
    if base_path is None:
        return False
    try:
        with Image.open(path) as pic_img, Image.open(base_path) as base_img:
            return pic_img.width == base_img.width and pic_img.height >= base_img.height + 200
    except Exception:
        return False


def organize_root_shelf_files(shelves_root: Path, dry_run: bool) -> tuple[int, int, int, int]:
    moved = deduped = conflicts = deleted = 0
    for source in sorted(shelves_root.glob("*.png")):
        area = classify_shelf_file(source.name)
        if not area:
            continue
        if looks_like_combo_image(source, shelves_root, area):
            deleted += delete_source(source, dry_run)
            continue
        destination = shelves_root / area / "shelf_pics" / source.name
        m, d, c = move_or_dedupe(source, destination, dry_run)
        moved += m
        deduped += d
        conflicts += c
    return moved, deduped, conflicts, deleted


def organize_legacy_marked(shelves_root: Path, dry_run: bool) -> tuple[int, int, int, int]:
    moved = deduped = conflicts = deleted = 0
    marked_root = shelves_root / "marked"
    for area in SHELF_PATTERNS:
        source_dir = marked_root / area
        if not source_dir.exists():
            continue
        for source in sorted(source_dir.glob("*.png")):
            if looks_like_combo_image(source, shelves_root, area):
                deleted += delete_source(source, dry_run)
                continue
            destination = shelves_root / area / "shelf_pics" / source.name
            m, d, c = move_or_dedupe(source, destination, dry_run)
            moved += m
            deduped += d
            conflicts += c
    return moved, deduped, conflicts, deleted


def organize_legacy_maps(shelves_root: Path, dry_run: bool) -> tuple[int, int, int, int]:
    moved = deduped = conflicts = deleted = 0
    maps_root = shelves_root / "maps"
    for area in SHELF_PATTERNS:
        source_dir = maps_root / area
        if not source_dir.exists():
            continue

        bases_dir = source_dir / "bases"
        if bases_dir.exists():
            for source in sorted(bases_dir.glob("*.png")):
                destination = shelves_root / area / "shelf_pics" / source.name
                m, d, c = move_or_dedupe(source, destination, dry_run)
                moved += m
                deduped += d
                conflicts += c

        for source in sorted(source_dir.iterdir()):
            if not source.is_file():
                continue
            map_area = classify_map_file(source.name)
            if map_area:
                destination = shelves_root / map_area / "maps" / source.name
                m, d, c = move_or_dedupe(source, destination, dry_run)
                moved += m
                deduped += d
                conflicts += c
                continue

            area_for_shelf = classify_shelf_file(source.name)
            if area_for_shelf:
                if looks_like_combo_image(source, shelves_root, area_for_shelf):
                    deleted += delete_source(source, dry_run)
                    continue
                destination = shelves_root / area_for_shelf / "shelf_pics" / source.name
                m, d, c = move_or_dedupe(source, destination, dry_run)
                moved += m
                deduped += d
                conflicts += c
    return moved, deduped, conflicts, deleted


def organize_legacy_originals(shelves_root: Path, dry_run: bool) -> tuple[int, int, int, int]:
    moved = deduped = conflicts = deleted = 0
    originals_root = shelves_root / "originals"
    if not originals_root.exists():
        return moved, deduped, conflicts, deleted

    for legacy_dir_name, area in (("office_map_bases", "office"), ("garage_map_bases", "garage")):
        source_dir = originals_root / legacy_dir_name
        if not source_dir.exists():
            continue
        for source in sorted(source_dir.glob("*.png")):
            destination = shelves_root / area / "shelf_pics" / source.name
            m, d, c = move_or_dedupe(source, destination, dry_run)
            moved += m
            deduped += d
            conflicts += c

    for source in sorted(originals_root.iterdir()):
        if not source.is_file():
            continue
        map_area = classify_map_file(source.name)
        if map_area:
            destination = shelves_root / map_area / "maps" / source.name
            m, d, c = move_or_dedupe(source, destination, dry_run)
            moved += m
            deduped += d
            conflicts += c
            continue

        area = classify_shelf_file(source.name)
        if not area:
            continue
        destination = shelves_root / area / "originals" / source.name
        m, d, c = move_or_dedupe(source, destination, dry_run)
        moved += m
        deduped += d
        conflicts += c

    return moved, deduped, conflicts, deleted


def organize_current_area_bases(shelves_root: Path, dry_run: bool) -> tuple[int, int, int, int]:
    moved = deduped = conflicts = deleted = 0
    for area in SHELF_PATTERNS:
        source_dir = shelves_root / area / "originals" / "bases"
        if not source_dir.exists():
            continue
        for source in sorted(source_dir.glob("*.png")):
            destination = shelves_root / area / "shelf_pics" / source.name
            m, d, c = move_or_dedupe(source, destination, dry_run)
            moved += m
            deduped += d
            conflicts += c
    return moved, deduped, conflicts, deleted


def organize_shelves(shelves_root: Path, dry_run: bool = False) -> int:
    ensure_area_layout(shelves_root)

    moved = deduped = conflicts = deleted = pruned = 0
    for organizer in (
        organize_root_shelf_files,
        organize_legacy_marked,
        organize_legacy_maps,
        organize_legacy_originals,
        organize_current_area_bases,
    ):
        m, d, c, x = organizer(shelves_root, dry_run)
        moved += m
        deduped += d
        conflicts += c
        deleted += x

    pruned += prune_empty_legacy_dirs(shelves_root, dry_run)
    print(
        f"\nDone. moved={moved} deduped={deduped} deleted={deleted} pruned={pruned} conflicts={conflicts} dry_run={dry_run}"
    )
    return 0 if conflicts == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Organize shelf assets into area/maps, area/originals, and area/shelf_pics.")
    parser.add_argument("shelves_root", nargs="?", default="static/shelves", help="Path to the static/shelves root")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without moving or deleting files")
    args = parser.parse_args()

    shelves_root = Path(args.shelves_root).resolve()
    if not shelves_root.exists():
        print(f"Missing shelves root: {shelves_root}")
        return 2

    return organize_shelves(shelves_root=shelves_root, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
