"""Thicken the green highlight outline on saved shelf photos, in place.

Works on the marked photo itself instead of redrawing on the original, so
rotated boxes, photos whose original has different dimensions, and photos that
contain real green objects (garage floor tape) are all handled: the script
picks the one green blob that looks like the highlight outline and dilates only
that blob from ~6px to ~12px.

Already-thick outlines are left alone, so re-running is safe.
Pass --apply to write; default is a dry run.
"""
import sys
from collections import deque
from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else '/opt/sweetshelves')
SHELVES = ROOT / 'static' / 'shelves'
APPLY = '--apply' in sys.argv
ONLY = [a for a in sys.argv[2:] if not a.startswith('-')]
GROW = 3           # pixels added on each side: 6px stroke -> 12px
THICK_ENOUGH = 9.0  # estimated stroke width at or above which we skip


def green_mask(img):
    w, h = img.size
    px = img.convert('RGB').load()
    mask = bytearray(w * h)
    for y in range(h):
        row = y * w
        for x in range(w):
            r, g, b = px[x, y]
            if r < 60 and g > 200 and b < 60:
                mask[row + x] = 1
    return mask, w, h


def components(mask, w, h):
    seen = bytearray(len(mask))
    out = []
    for start in range(len(mask)):
        if not mask[start] or seen[start]:
            continue
        queue = deque([start])
        seen[start] = 1
        pixels = []
        while queue:
            idx = queue.popleft()
            pixels.append(idx)
            x, y = idx % w, idx // w
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        n = ny * w + nx
                        if mask[n] and not seen[n]:
                            seen[n] = 1
                            queue.append(n)
        out.append(pixels)
    return out


def describe(pixels, w):
    xs = [p % w for p in pixels]
    ys = [p // w for p in pixels]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    perimeter = 2 * (bw + bh)
    return {
        'box': (x0, y0, x1, y1),
        'bbox_area': bw * bh,
        'count': len(pixels),
        'fill': len(pixels) / float(bw * bh),
        'stroke': len(pixels) / float(perimeter) if perimeter else 0.0,
        'small_side': min(bw, bh),
    }


def stroke_width(mask, w, box):
    """Rough stroke thickness: green pixels inside the box spread over the
    box perimeter. Robust to the 1px gaps PIL leaves in a drawn outline."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    total = sum(1 for y in range(y0, y1 + 1) for x in range(x0, x1 + 1) if mask[y * w + x])
    return total / float(2 * (bw + bh))


def pick_outline(comps, w, h):
    """The highlight outline: biggest bbox among hollow, reasonably large blobs."""
    best = None
    for pixels in comps:
        info = describe(pixels, w)
        if info['small_side'] < 20:
            continue           # tape stripe, speck, thin sliver
        if info['fill'] > 0.35:
            continue           # a filled green object, not an outline
        if info['bbox_area'] < 0.01 * w * h:
            continue           # too small to be the highlight
        if best is None or info['bbox_area'] > best[1]['bbox_area']:
            best = (pixels, info)
    return best


def main():
    grown = skipped = 0
    for pics_dir in sorted(SHELVES.glob('*/shelf_pics')):
        for path in sorted(pics_dir.glob('*.png')):
            if ONLY and path.stem not in ONLY:
                continue
            img = Image.open(path).convert('RGB')
            mask, w, h = green_mask(img)
            if sum(mask) < 40:
                continue
            picked = pick_outline(components(mask, w, h), w, h)
            if picked is None:
                print(f'SKIP {path.relative_to(SHELVES)}: no outline-shaped green blob')
                skipped += 1
                continue
            pixels, info = picked
            # The ring can be broken into several blobs, so take every green
            # pixel inside the highlight's bounding box as one outline.
            x0, y0, x1, y1 = info['box']
            ring = [i for i in range(len(mask))
                    if mask[i] and x0 <= i % w <= x1 and y0 <= i // w <= y1]
            stroke = stroke_width(mask, w, info['box'])
            if stroke >= THICK_ENOUGH:
                continue  # already thickened
            blob = Image.new('L', (w, h), 0)
            bp = blob.load()
            for idx in ring:
                bp[idx % w, idx // w] = 255
            fat = blob.filter(ImageFilter.MaxFilter(2 * GROW + 1))
            img.paste(Image.new('RGB', (w, h), (0, 255, 0)), (0, 0), fat)
            print(f'{"WRITE" if APPLY else "would write"} {path.relative_to(SHELVES)} '
                  f'box={info["box"]} stroke~{stroke:.1f}')
            if APPLY:
                backup = path.with_suffix('.png.thin6')
                if not backup.exists():
                    backup.write_bytes(path.read_bytes())
                img.save(path)
            grown += 1
    print(f'\n{grown} photo(s) {"updated" if APPLY else "to update"}, {skipped} skipped')


main()
