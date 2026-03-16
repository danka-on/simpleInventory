from __future__ import annotations

import math
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
SHELF_DIR = ROOT / "static" / "shelves"
AREA_DIR = SHELF_DIR / "garage"
ORIGINALS_DIR = AREA_DIR / "originals"
MAPS_DIR = AREA_DIR / "maps"
SHELF_PICS_DIR = AREA_DIR / "shelf_pics"
MAP_SOURCE = MAPS_DIR / "garagemap.png"
LEGACY_MAP_SOURCE = SHELF_DIR / "maps" / "garage" / "garagemap.png"
# "bases" now live in shelf_pics.
BASES_DIR = SHELF_PICS_DIR
LEGACY_BASES_DIR = SHELF_DIR / "maps" / "garage" / "bases"
LEGACY_ORIGINALS_DIR = SHELF_DIR / "originals"
FONT_PATH = Path(r"C:\Windows\Fonts\arial.ttf")

TARGETS = {
    "misc1": {
        "rect": (6, 303, 169, 387),
        "label": "MISC1",
        "arrow": ((214, 335), (169, 345)),
        "codes": ["misc1"],
    },
    "gfloor1": {
        "rect": (4, 399, 123, 503),
        "label": "gFloor1",
        "arrow": ((172, 445), (123, 445)),
        "codes": ["gfloor1"],
    },
    "gfloor2": {
        "rect": (4, 519, 123, 621),
        "label": "gFloor2",
        "arrow": ((171, 570), (123, 570)),
        "codes": ["gfloor2"],
    },
    "gfloor3": {
        "rect": (4, 628, 124, 756),
        "label": "gFloor3",
        "arrow": ((170, 692), (124, 692)),
        "codes": ["gfloor3"],
    },
    "gfloor4": {
        "rect": (138, 711, 197, 770),
        "label": "4",
        "arrow": ((245, 682), (167, 711)),
        "codes": ["gfloor4"],
    },
    "gfloor5": {
        "rect": (202, 711, 262, 770),
        "label": "5",
        "arrow": ((308, 682), (232, 711)),
        "codes": ["gfloor5"],
    },
    "gfloor6": {
        "rect": (268, 709, 329, 771),
        "label": "6",
        "arrow": ((371, 682), (299, 709)),
        "codes": ["gfloor6"],
    },
    "gfloor7": {
        "rect": (333, 708, 403, 770),
        "label": "7",
        "arrow": ((435, 682), (368, 708)),
        "codes": ["gfloor7"],
    },
    "gmid2": {
        "rect": (366, 410, 465, 511),
        "label": "GMID2",
        "arrow": ((514, 431), (465, 455)),
        "codes": ["gmid2"],
    },
    "gmid1": {
        "rect": (368, 524, 466, 609),
        "label": "GMID1",
        "arrow": ((516, 566), (466, 568)),
        "codes": ["gmid1"],
    },
    "gr7": {
        "rect": (464, 169, 587, 389),
        "label": "GR7",
        "arrow": ((633, 235), (587, 251)),
        "codes": [f"gr7s{i}" for i in range(1, 5)],
    },
    "gr6": {
        "rect": (479, 409, 581, 636),
        "label": "GR6",
        "arrow": ((633, 524), (581, 542)),
        "codes": [f"gr6s{i}" for i in range(1, 5)],
    },
    "gr4": {
        "rect": (592, 28, 809, 127),
        "label": "GR4",
        "arrow": ((543, 78), (592, 78)),
        "codes": [f"gr4s{i}" for i in range(1, 5)],
    },
    "gr3": {
        "rect": (733, 148, 816, 340),
        "label": "GR3",
        "arrow": ((679, 218), (733, 245)),
        "codes": [f"gr3s{i}" for i in range(1, 6)],
    },
    "gr2": {
        "rect": (736, 506, 814, 634),
        "label": "GR2",
        "arrow": ((687, 586), (736, 586)),
        "codes": [f"gr2s{i}" for i in range(1, 6)],
    },
    "gr1": {
        "rect": (624, 671, 812, 751),
        "label": "GR1",
        "arrow": ((573, 742), (624, 719)),
        "codes": [f"gr1s{i}" for i in range(1, 7)],
    },
}


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if FONT_PATH.exists():
        return ImageFont.truetype(str(FONT_PATH), size)
    return ImageFont.load_default()


def fit_font(label: str, rect: tuple[int, int, int, int]) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]
    max_size = max(16, min(40, int(min(width * 0.42, height * 0.5))))
    min_size = 12
    measure = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    for size in range(max_size, min_size - 1, -2):
        font = load_font(size)
        bbox = measure.textbbox((0, 0), label, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        if text_w <= width - 10 and text_h <= height - 10:
            return font
    return load_font(min_size)


def draw_arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int]) -> None:
    sx, sy = start
    ex, ey = end
    angle = math.atan2(ey - sy, ex - sx)
    shaft_width = 12
    head_length = 28
    head_half = 13
    color = (220, 38, 38, 255)

    draw.line((sx, sy, ex, ey), fill=color, width=shaft_width)

    back_x = ex - head_length * math.cos(angle)
    back_y = ey - head_length * math.sin(angle)
    left = (
        back_x + head_half * math.sin(angle),
        back_y - head_half * math.cos(angle),
    )
    right = (
        back_x - head_half * math.sin(angle),
        back_y + head_half * math.cos(angle),
    )
    draw.polygon([(ex, ey), left, right], fill=color)


def find_existing_source(code: str) -> Path | None:
    candidates = [
        BASES_DIR / f"{code}.png",
        LEGACY_BASES_DIR / f"{code}.png",
        ORIGINALS_DIR / f"{code}.png",
        ORIGINALS_DIR / f"{code.upper()}.png",
        SHELF_PICS_DIR / f"{code}.png",
        SHELF_PICS_DIR / f"{code.upper()}.png",
        SHELF_DIR / f"{code}.png",
        SHELF_DIR / f"{code.upper()}.png",
        LEGACY_ORIGINALS_DIR / f"{code}.png",
        LEGACY_ORIGINALS_DIR / f"{code.upper()}.png",
        ORIGINALS_DIR / f"{code}.png",
        ORIGINALS_DIR / f"{code.upper()}.png",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def resolve_map_source() -> Path:
    if MAP_SOURCE.exists():
        return MAP_SOURCE
    return LEGACY_MAP_SOURCE


def ensure_base_sources(display_codes: list[str]) -> None:
    BASES_DIR.mkdir(parents=True, exist_ok=True)
    for code in display_codes:
        target = BASES_DIR / f"{code}.png"
        if target.exists():
            continue
        source = find_existing_source(code)
        if source is None:
            continue
        shutil.copy2(source, target)


def build_map_asset(key: str, config: dict[str, object]) -> Image.Image:
    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    base = Image.open(resolve_map_source()).convert("RGBA")
    draw = ImageDraw.Draw(base, "RGBA")
    rect = config["rect"]
    label = str(config["label"])
    arrow_start, arrow_end = config["arrow"]

    draw.rounded_rectangle(
        rect,
        radius=8,
        fill=(255, 235, 59, 54),
        outline=(255, 205, 0, 255),
        width=10,
    )

    font = fit_font(label, rect)
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    text_x = rect[0] + ((rect[2] - rect[0] - text_w) // 2)
    text_y = rect[1] + ((rect[3] - rect[1] - text_h) // 2) - 2
    draw.text((text_x, text_y), label, fill=(33, 33, 33, 220), font=font)

    draw_arrow(draw, arrow_start, arrow_end)

    out_path = MAPS_DIR / f"garagemap_{key}.png"
    base.save(out_path)
    return base


def main() -> None:
    for key, config in TARGETS.items():
        build_map_asset(key, config)

    print("Generated garage map assets for", ", ".join(sorted(TARGETS)))
    print("Shelf/map combo images are no longer generated.")


if __name__ == "__main__":
    main()
