"""Redraw the green highlight box on saved shelf photos at double thickness.

For each static/shelves/<area>/shelf_pics/<code>.png that carries a pure-green
axis-aligned rectangle outline, re-draw that same rectangle on the clean
original at width 12 (was 6). Skips anything that does not look like a single
axis-aligned outline. Pass --apply to write; default is a dry run.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else '/opt/sweetshelves')
SHELVES = ROOT / 'static' / 'shelves'
APPLY = '--apply' in sys.argv
NEW_WIDTH = 12


def green_mask(img):
    px = img.convert('RGB').load()
    w, h = img.size
    pts = []
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if r < 60 and g > 200 and b < 60:
                pts.append((x, y))
    return pts, w, h


def main():
    changed = skipped = 0
    for pics_dir in sorted(SHELVES.glob('*/shelf_pics')):
        originals = pics_dir.parent / 'originals'
        for marked_path in sorted(pics_dir.glob('*.png')):
            orig_path = originals / marked_path.name
            if not orig_path.exists():
                continue
            marked = Image.open(marked_path)
            orig = Image.open(orig_path)
            if marked.size != orig.size:
                print(f'SKIP {marked_path.name}: size mismatch {marked.size} vs {orig.size}')
                skipped += 1
                continue
            pts, w, h = green_mask(marked)
            if len(pts) < 40:
                continue  # no highlight drawn on this photo
            x0 = min(p[0] for p in pts)
            x1 = max(p[0] for p in pts)
            y0 = min(p[1] for p in pts)
            y1 = max(p[1] for p in pts)
            if x1 - x0 < 12 or y1 - y0 < 12:
                print(f'SKIP {marked_path.name}: box too small ({x1-x0}x{y1-y0})')
                skipped += 1
                continue
            # Every green pixel must sit within 10px of the bbox border, or it is
            # not a plain axis-aligned outline (rotated box, handles, scribbles).
            def on_border(p):
                x, y = p
                return (
                    x - x0 <= 10 or x1 - x <= 10 or y - y0 <= 10 or y1 - y <= 10
                )
            stray = sum(1 for p in pts if not on_border(p))
            if stray > len(pts) * 0.02:
                print(f'SKIP {marked_path.name}: {stray}/{len(pts)} green px off the border')
                skipped += 1
                continue
            out = orig.convert('RGB')
            draw = ImageDraw.Draw(out)
            # Keep the outer edge where it was: the old 6px stroke was centred on
            # the rect, so its outer edge is the bbox. Draw inward from there.
            inset = NEW_WIDTH // 2
            draw.rectangle(
                [x0 + inset, y0 + inset, x1 - inset, y1 - inset],
                outline=(0, 255, 0),
                width=NEW_WIDTH,
            )
            print(f'{"WRITE" if APPLY else "would write"} {marked_path.relative_to(SHELVES)} box=({x0},{y0})-({x1},{y1})')
            if APPLY:
                backup = marked_path.with_suffix('.png.thin6')
                if not backup.exists():
                    backup.write_bytes(marked_path.read_bytes())
                out.save(marked_path)
            changed += 1
    print(f'\n{changed} photo(s) {"updated" if APPLY else "to update"}, {skipped} skipped')


main()
