"""Generate a clean office map from the shelf photos plus per-rack highlight assets.

Layout proportions follow the hand-drawn officemap.jpg (830x776); everything is
drawn at SCALE x and downsampled to OUT_WIDTH so text stays crisp.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

try:
    import numpy as np
except ImportError:  # pragma: no cover - label clean-up is skipped without numpy
    np = None

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
SHELF_DIR = ROOT / "static" / "shelves"
AREA_DIR = SHELF_DIR / "office"
ORIGINALS_DIR = AREA_DIR / "originals"
MAPS_DIR = AREA_DIR / "maps"
MAP_OUT = MAPS_DIR / "officemap.png"
FONT_DIR = Path(r"C:\Windows\Fonts")

SCALE = 2
W, H = 830 * SCALE, 776 * SCALE
OUT_WIDTH = 1245  # 1.5x of the hand-drawn map

# Palette
FLOOR = (247, 244, 238)
WALL = (43, 43, 43)
WINDOW = (124, 196, 245)
FURN_FILL = (233, 213, 184)
FURN_LINE = (181, 135, 90)
FURN_TEXT = (122, 78, 36)
RACK_LINE = (70, 70, 70)
RACK_PILL = (31, 41, 55)
FLOOR_LINE = (34, 164, 93)
FLOOR_PILL = (20, 120, 66)
DOOR = (45, 127, 214)
TEXT_DARK = (40, 40, 40)
HILITE = (255, 205, 0)
ARROW = (220, 38, 38)


def S(*vals: float) -> tuple[int, ...]:
    return tuple(int(round(v * SCALE)) for v in vals)


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    name = "arialbd.ttf" if bold else "arial.ttf"
    p = FONT_DIR / name
    if p.exists():
        return ImageFont.truetype(str(p), int(size * SCALE))
    for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(cand).exists():
            return ImageFont.truetype(cand, int(size * SCALE))
    return ImageFont.load_default()


# rect = (x0, y0, x1, y1) in hand-drawn 830x776 space. photo = originals file stem.
RACKS = {
    "or3": {"rect": (8, 131, 82, 294), "label": "OR3", "photo": ["or3s3", "or3s4"],
            "codes": [f"or3s{i}" for i in range(1, 7)]},
    "or2": {"rect": (8, 309, 82, 477), "label": "OR2", "photo": ["or2s3", "or2s4"],
            "codes": [f"or2s{i}" for i in range(1, 7)]},
    "or1": {"rect": (8, 496, 111, 759), "label": "OR1", "photo": ["or1s3", "or1s6", "or1s1"],
            "codes": [f"or1s{i}" for i in range(1, 7)]},
    "omr2": {"rect": (288, 271, 420, 446), "label": "OMR2", "photo": "omr2s1",
             "codes": [f"omr2s{i}" for i in range(1, 6)]},
    "omr1": {"rect": (288, 478, 420, 629), "label": "OMR1", "photo": "omr1s1",
             "codes": [f"omr1s{i}" for i in range(1, 6)]},
    "or6": {"rect": (673, 269, 808, 407), "label": "OR6", "photo": "or6s1",
            "codes": [f"or6s{i}" for i in range(1, 7)]},
    "or5": {"rect": (673, 432, 808, 588), "label": "OR5", "photo": "or5s1",
            "codes": [f"or5s{i}" for i in range(1, 6)]},
    "or4": {"rect": (673, 612, 808, 750), "label": "OR4", "photo": "or4s1",
            "codes": [f"or4s{i}" for i in range(1, 6)]},
}
FLOORS = {
    "ofloor6": {"rect": (430, 274, 656, 326), "label": "oFloor6", "photo": "ofloor6"},
    "ofloor5": {"rect": (430, 347, 548, 426), "label": "oFloor5", "photo": "ofloor5"},
    "ofloor4": {"rect": (430, 434, 548, 533), "label": "oFloor4", "photo": "ofloor4"},
    "ofloor3": {"rect": (430, 540, 548, 628), "label": "oFloor3", "photo": "ofloor3"},
    "ofloor1": {"rect": (299, 648, 424, 697), "label": "oFloor1", "photo": "ofloor1"},
    "ofloor2": {"rect": (466, 706, 657, 760), "label": "oFloor2", "photo": "ofloor2"},
}
for cfg in FLOORS.values():
    cfg["codes"] = [next(k for k, v in FLOORS.items() if v is cfg)]

TARGETS = {**RACKS, **FLOORS}

DESK = (330, 158, 780, 265)
PRINTER = (740, 8, 822, 105)
CHAIR = (505, 62, 590, 128)
WINDOWS = [(130, 0, 310, 16), (530, 0, 727, 16)]
DOOR_HINGE = (160, 776)
DOOR_LEN = 118


def green_label_bbox(img: Image.Image) -> tuple[int, int, int, int] | None:
    """Bounding box of the pure-green shelf label baked into some rack photos."""
    if np is None:
        return None
    a = np.asarray(img.convert("RGB")).astype(int)
    m = (a[..., 1] > 180) & (a[..., 0] < 90) & (a[..., 2] < 90)
    ys, xs = np.where(m)
    if len(xs) < 200:
        return None
    return int(xs.min()) - 4, int(ys.min()) - 4, int(xs.max()) + 5, int(ys.max()) + 5


def load_photo(spec) -> Image.Image | None:
    """Load a rack photo. A list means: same frame, patch the baked-in label
    band of the first photo with pixels from a sibling whose label sits elsewhere."""
    stems = [spec] if isinstance(spec, str) else list(spec)
    paths = [ORIGINALS_DIR / f"{st}.png" for st in stems]
    paths = [pth for pth in paths if pth.exists()]
    if not paths:
        return None
    base = Image.open(paths[0]).convert("RGB")
    box = green_label_bbox(base)
    for pth in paths[1:]:
        if box is None:
            break
        with Image.open(pth) as sib:
            sib = sib.convert("RGB")
        if sib.size != base.size:
            continue
        other = green_label_bbox(sib)
        if other and not (other[2] < box[0] or other[0] > box[2] or other[3] < box[1] or other[1] > box[3]):
            continue  # sibling label overlaps ours; try the next one
        base.paste(sib.crop(box), box[:2])
        box = None
    return base


def cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    tw, th = size
    sw, sh = img.size
    scale = max(tw / sw, th / sh)
    nw, nh = max(tw, int(sw * scale + 0.5)), max(th, int(sh * scale + 0.5))
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return m


def pill(draw: ImageDraw.ImageDraw, center: tuple[int, int], text: str, fill, fnt, pad=(10, 5)) -> None:
    bbox = draw.textbbox((0, 0), text, font=fnt)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    px, py = pad[0] * SCALE, pad[1] * SCALE
    x0 = center[0] - tw // 2 - px
    y0 = center[1] - th // 2 - py
    x1 = center[0] + tw // 2 + px
    y1 = center[1] + th // 2 + py
    draw.rounded_rectangle((x0, y0, x1, y1), radius=(th + 2 * py) // 2, fill=fill)
    draw.text((center[0] - tw // 2 - bbox[0], center[1] - th // 2 - bbox[1]), text, font=fnt, fill=(255, 255, 255))


def draw_card(base: Image.Image, cfg: dict, line, pill_fill, line_w: int) -> None:
    rect = S(*cfg["rect"])
    x0, y0, x1, y1 = rect
    size = (x1 - x0, y1 - y0)
    radius = 6 * SCALE
    photo = load_photo(cfg["photo"])
    if photo is not None:
        ph = cover(photo, size)
        # soften the photo so labels and outlines stay dominant
        ph = Image.blend(ph, Image.new("RGB", size, (255, 255, 255)), 0.12)
        base.paste(ph, (x0, y0), rounded_mask(size, radius))
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle(rect, radius=radius, outline=line, width=line_w)

    w, h = size
    label = cfg["label"]
    fs = 15 if w < 90 * SCALE else 18
    fnt = font(fs)
    bbox = draw.textbbox((0, 0), label, font=fnt)
    tw = bbox[2] - bbox[0]
    # Tall narrow cards: pill inside near the bottom. Wide/short: centered.
    if w < tw + 30 * SCALE:
        fnt = font(12)
    cy = y1 - 16 * SCALE if h > 70 * SCALE else (y0 + y1) // 2
    pill(draw, ((x0 + x1) // 2, cy), label, pill_fill, fnt)


def draw_base() -> Image.Image:
    img = Image.new("RGB", (W, H), FLOOR)
    draw = ImageDraw.Draw(img)

    # faint floor tile grid
    step = 40 * SCALE
    for x in range(0, W, step):
        draw.line((x, 0, x, H), fill=(238, 233, 224), width=1)
    for y in range(0, H, step):
        draw.line((0, y, W, y), fill=(238, 233, 224), width=1)

    # furniture
    for rect, label, fs in ((S(*DESK), "Office desk (stalas)", 22), (S(*PRINTER), "Printer", 12)):
        draw.rounded_rectangle(rect, radius=6 * SCALE, fill=FURN_FILL, outline=FURN_LINE, width=3 * SCALE)
        fnt = font(fs, bold=False)
        bbox = draw.textbbox((0, 0), label, font=fnt)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
        draw.text((cx - tw // 2 - bbox[0], cy - th // 2 - bbox[1]), label, font=fnt, fill=FURN_TEXT)
    # chair (the small L next to the desk on the hand-drawn map)
    cx0, cy0, cx1, cy1 = S(*CHAIR)
    draw.rounded_rectangle((cx0, cy0 + 20 * SCALE, cx1, cy1), radius=8 * SCALE, fill=FURN_FILL, outline=FURN_LINE, width=3 * SCALE)
    draw.rounded_rectangle((cx0, cy0, cx0 + 14 * SCALE, cy1), radius=6 * SCALE, fill=FURN_FILL, outline=FURN_LINE, width=3 * SCALE)
    fnt = font(11, bold=False)
    bbox = draw.textbbox((0, 0), "chair", font=fnt)
    draw.text(((cx0 + cx1) // 2 - (bbox[2] - bbox[0]) // 2 + 6 * SCALE, (cy0 + cy1) // 2 - (bbox[3] - bbox[1]) // 2 + 8 * SCALE), "chair", font=fnt, fill=FURN_TEXT)

    # storage
    for cfg in RACKS.values():
        draw_card(img, cfg, RACK_LINE, RACK_PILL, 3 * SCALE)
    for cfg in FLOORS.values():
        draw_card(img, cfg, FLOOR_LINE, FLOOR_PILL, 4 * SCALE)
    draw = ImageDraw.Draw(img)

    # walls
    wall_w = 7 * SCALE
    draw.rectangle((0, 0, W - 1, H - 1), outline=WALL, width=wall_w)
    for rect in WINDOWS:
        x0, _, x1, _ = S(*rect)
        draw.rectangle((x0, 0, x1, wall_w + 3 * SCALE), fill=WINDOW, outline=WALL, width=SCALE)
        draw.line((x0, (wall_w + 3 * SCALE) // 2, x1, (wall_w + 3 * SCALE) // 2), fill=(255, 255, 255), width=SCALE)

    # door: gap in the bottom wall + swing arc
    hx, hy = S(*DOOR_HINGE)
    dl = DOOR_LEN * SCALE
    draw.rectangle((hx, H - wall_w, hx + dl, H - 1), fill=FLOOR)
    draw.arc((hx - dl, hy - dl, hx + dl, hy + dl), start=270, end=360, fill=DOOR, width=3 * SCALE)
    ang = math.radians(-58)
    draw.line((hx, hy, hx + dl * math.cos(ang), hy + dl * math.sin(ang)), fill=DOOR, width=5 * SCALE)
    fnt = font(13, bold=False)
    draw.text((hx + 40 * SCALE, hy - 38 * SCALE), "door (durys)", font=fnt, fill=DOOR)

    # title
    draw.text(S(22, 30), "OFFICE", font=font(26), fill=TEXT_DARK)
    draw.text(S(22, 66), "storage map", font=font(13, bold=False), fill=(110, 110, 110))
    return img


def arrow_for(rect: tuple[int, int, int, int]) -> tuple[tuple[int, int], tuple[int, int]]:
    """Arrow pointing at the rect from the direction of the room's open middle."""
    x0, y0, x1, y1 = rect
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    # aim from the map centre, but bias vertically so arrows on the side
    # columns come in diagonally rather than crossing other cards
    tx, ty = W / 2, H / 2
    dx, dy = tx - cx, ty - cy
    n = math.hypot(dx, dy) or 1
    dx, dy = dx / n, dy / n
    # exit point on rect boundary along (dx, dy)
    tx_ = ((x1 if dx > 0 else x0) - cx) / dx if abs(dx) > 1e-6 else math.inf
    ty_ = ((y1 if dy > 0 else y0) - cy) / dy if abs(dy) > 1e-6 else math.inf
    t = min(tx_, ty_)
    ex, ey = cx + dx * t, cy + dy * t
    length = 70 * SCALE
    sx, sy = ex + dx * length, ey + dy * length
    return (int(sx), int(sy)), (int(ex), int(ey))


def draw_arrow(draw: ImageDraw.ImageDraw, start, end) -> None:
    sx, sy = start
    ex, ey = end
    angle = math.atan2(ey - sy, ex - sx)
    shaft, head_len, head_half = 8 * SCALE, 20 * SCALE, 10 * SCALE
    bx, by = ex - head_len * math.cos(angle), ey - head_len * math.sin(angle)
    draw.line((sx, sy, bx, by), fill=ARROW, width=shaft)
    left = (bx + head_half * math.sin(angle), by - head_half * math.cos(angle))
    right = (bx - head_half * math.sin(angle), by + head_half * math.cos(angle))
    draw.polygon([(ex, ey), left, right], fill=ARROW)


def finish(img: Image.Image) -> Image.Image:
    out_h = int(round(img.height * OUT_WIDTH / img.width))
    out = img.resize((OUT_WIDTH, out_h), Image.LANCZOS)
    # 256-colour palette keeps each asset ~300 KB instead of ~750 KB
    return out.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)


def build_highlight(base: Image.Image, key: str, cfg: dict) -> Image.Image:
    rect = S(*cfg["rect"])
    # dim everything except the target
    dim = Image.blend(base, Image.new("RGB", base.size, (255, 255, 255)), 0.45)
    pad = 6 * SCALE
    region = (rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad)
    dim.paste(base.crop(region), region[:2])
    draw = ImageDraw.Draw(dim)
    draw.rounded_rectangle(region, radius=10 * SCALE, outline=HILITE, width=6 * SCALE)
    glow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rounded_rectangle((region[0] - 8 * SCALE, region[1] - 8 * SCALE, region[2] + 8 * SCALE, region[3] + 8 * SCALE),
                         radius=14 * SCALE, outline=HILITE + (140,), width=10 * SCALE)
    glow = glow.filter(ImageFilter.GaussianBlur(6 * SCALE))
    dim = Image.alpha_composite(dim.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(dim)
    draw_arrow(draw, *arrow_for(rect))
    # big label near the arrow start
    fnt = font(20)
    label = cfg["label"]
    (sx, sy), _ = arrow_for(rect)
    bbox = draw.textbbox((0, 0), label, font=fnt)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    # place label beyond the arrow start, away from the target
    ex, ey = arrow_for(rect)[1]
    ddx, ddy = sx - ex, sy - ey
    n = math.hypot(ddx, ddy) or 1
    lx = sx + ddx / n * (tw / 2 + 14 * SCALE)
    ly = sy + ddy / n * (th / 2 + 14 * SCALE)
    lx = min(max(lx, tw / 2 + 4 * SCALE), W - tw / 2 - 4 * SCALE)
    ly = min(max(ly, th / 2 + 4 * SCALE), H - th / 2 - 4 * SCALE)
    pill(draw, (int(lx), int(ly)), label, ARROW, fnt, pad=(12, 7))
    return dim


def main() -> None:
    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    base = draw_base()
    finish(base).save(MAP_OUT, optimize=True)
    for key, cfg in TARGETS.items():
        finish(build_highlight(base, key, cfg)).save(MAPS_DIR / f"officemap_{key}.png", optimize=True)
    print("Generated", MAP_OUT.name, "and", len(TARGETS), "highlight assets in", MAPS_DIR)


if __name__ == "__main__":
    main()
