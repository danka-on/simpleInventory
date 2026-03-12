from __future__ import annotations

import math
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
SHELF_DIR = ROOT / "static" / "shelves"
ORIGINALS_DIR = SHELF_DIR / "originals"
MAP_SOURCE = ORIGINALS_DIR / "garagemap.png"
BASES_DIR = ORIGINALS_DIR / "garage_map_bases"
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
        "rect": (137, 704, 224, 763),
        "label": "4",
        "arrow": ((250, 673), (181, 704)),
        "codes": ["gfloor4"],
    },
    "gfloor5": {
        "rect": (244, 708, 312, 760),
        "label": "5",
        "arrow": ((314, 674), (278, 708)),
        "codes": ["gfloor5"],
    },
    "gfloor6": {
        "rect": (329, 704, 397, 761),
        "label": "6",
        "arrow": ((397, 674), (362, 704)),
        "codes": ["gfloor6"],
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
        SHELF_DIR / f"{code}.png",
        SHELF_DIR / f"{code.upper()}.png",
        ORIGINALS_DIR / f"{code}.png",
        ORIGINALS_DIR / f"{code.upper()}.png",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


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
    base = Image.open(MAP_SOURCE).convert("RGBA")
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

    out_path = ORIGINALS_DIR / f"garagemap_{key}.png"
    base.save(out_path)
    return base


def build_composite(display_code: str, map_image: Image.Image, title_label: str) -> bool:
    source = BASES_DIR / f"{display_code}.png"
    if not source.exists():
        return False

    shelf = Image.open(source).convert("RGBA")

    caption_font = load_font(14)
    map_width = min(400, map_image.width)
    map_height = round(map_image.height * (map_width / map_image.width))
    map_resized = map_image.resize((map_width, map_height), Image.Resampling.LANCZOS)

    pad = 18
    label_h = 24
    canvas_w = shelf.width
    canvas_h = shelf.height + pad + label_h + map_resized.height + pad
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 255))
    canvas.alpha_composite(shelf, (0, 0))

    draw = ImageDraw.Draw(canvas)
    sep_y = shelf.height + 8
    draw.line((24, sep_y, canvas_w - 24, sep_y), fill=(214, 214, 214, 255), width=2)
    draw.text((24, shelf.height + 16), f"Garage map - {title_label} highlighted", fill=(72, 72, 72, 255), font=caption_font)

    map_x = (canvas_w - map_resized.width) // 2
    map_y = shelf.height + pad + label_h
    canvas.alpha_composite(map_resized, (map_x, map_y))
    canvas.save(SHELF_DIR / f"{display_code}.png")
    return True


def main() -> None:
    display_codes: list[str] = []
    for config in TARGETS.values():
        display_codes.extend(config["codes"])
    ensure_base_sources(display_codes)

    generated_composites: list[str] = []
    for key, config in TARGETS.items():
        map_image = build_map_asset(key, config)
        title_label = str(config["label"])
        for code in config["codes"]:
            if build_composite(code, map_image, title_label):
                generated_composites.append(code)

    print("Generated garage map assets for", ", ".join(sorted(TARGETS)))
    print("Updated shelf composites:", ", ".join(sorted(generated_composites)) or "(map assets only)")


if __name__ == "__main__":
    main()
