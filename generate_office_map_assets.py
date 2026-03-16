from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
SHELF_DIR = ROOT / "static" / "shelves"
AREA_DIR = SHELF_DIR / "office"
ORIGINALS_DIR = AREA_DIR / "originals"
MAPS_DIR = AREA_DIR / "maps"
SHELF_PICS_DIR = AREA_DIR / "shelf_pics"
MAP_SOURCE = MAPS_DIR / "officemap.jpg"
LEGACY_MAP_SOURCE = SHELF_DIR / "maps" / "office" / "officemap.jpg"
# "bases" now live in shelf_pics.
BASES_DIR = SHELF_PICS_DIR
LEGACY_BASES_DIR = SHELF_DIR / "maps" / "office" / "bases"
LEGACY_ORIGINALS_DIR = SHELF_DIR / "originals"
FONT_PATH = Path(r"C:\Windows\Fonts\arial.ttf")

TARGETS = {
    "or3": {
        "rect": (7, 131, 82, 294),
        "label": "OR3",
        "arrow": ((168, 131), (82, 200)),
        "codes": [f"or3s{i}" for i in range(1, 7)],
    },
    "or2": {
        "rect": (10, 309, 80, 477),
        "label": "OR2",
        "arrow": ((166, 319), (80, 382)),
        "codes": [f"or2s{i}" for i in range(1, 7)],
    },
    "or1": {
        "rect": (13, 496, 111, 759),
        "label": "OR1",
        "arrow": ((170, 465), (111, 520)),
        "codes": [f"or1s{i}" for i in range(1, 7)],
    },
    "omr2": {
        "rect": (288, 271, 420, 446),
        "label": "OMR2",
        "arrow": ((188, 198), (290, 302)),
        "codes": [f"omr2s{i}" for i in range(1, 6)],
    },
    "omr1": {
        "rect": (287, 478, 427, 629),
        "label": "OMR1",
        "arrow": ((183, 690), (287, 592)),
        "codes": [f"omr1s{i}" for i in range(1, 6)],
    },
    "ofloor6": {
        "rect": (423, 274, 656, 326),
        "label": "oFloor6",
        "arrow": ((684, 235), (611, 274)),
        "codes": ["ofloor6"],
    },
    "ofloor5": {
        "rect": (418, 347, 548, 426),
        "label": "oFloor5",
        "arrow": ((633, 353), (548, 386)),
        "codes": ["ofloor5"],
    },
    "ofloor4": {
        "rect": (423, 434, 542, 533),
        "label": "oFloor4",
        "arrow": ((623, 483), (542, 483)),
        "codes": ["ofloor4"],
    },
    "ofloor3": {
        "rect": (430, 540, 539, 628),
        "label": "oFloor3",
        "arrow": ((618, 615), (539, 584)),
        "codes": ["ofloor3"],
    },
    "ofloor1": {
        "rect": (299, 648, 424, 697),
        "label": "oFloor1",
        "arrow": ((173, 742), (300, 676)),
        "codes": ["ofloor1"],
    },
    "ofloor2": {
        "rect": (466, 706, 657, 760),
        "label": "oFloor2",
        "arrow": ((712, 744), (657, 734)),
        "codes": ["ofloor2"],
    },
    "or6": {
        "rect": (673, 269, 808, 407),
        "label": "OR6",
        "arrow": ((612, 225), (673, 315)),
        "codes": [f"or6s{i}" for i in range(1, 7)],
    },
    "or5": {
        "rect": (673, 432, 808, 588),
        "label": "OR5",
        "arrow": ((613, 608), (673, 522)),
        "codes": [f"or5s{i}" for i in range(1, 6)],
    },
    "or4": {
        "rect": (676, 612, 809, 750),
        "label": "OR4",
        "arrow": ((623, 760), (676, 694)),
        "codes": [f"or4s{i}" for i in range(1, 6)],
    },
}


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if FONT_PATH.exists():
        return ImageFont.truetype(str(FONT_PATH), size)
    return ImageFont.load_default()


def resolve_map_source() -> Path:
    if MAP_SOURCE.exists():
        return MAP_SOURCE
    return LEGACY_MAP_SOURCE


def fit_font(label: str, rect: tuple[int, int, int, int]) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]
    max_size = max(16, min(40, int(min(width * 0.38, height * 0.5))))
    min_size = 14
    for size in range(max_size, min_size - 1, -2):
        font = load_font(size)
        bbox = ImageDraw.Draw(Image.new("RGBA", (10, 10))).textbbox((0, 0), label, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        if text_w <= width - 12 and text_h <= height - 12:
            return font
    return load_font(min_size)


def draw_arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int]) -> None:
    sx, sy = start
    ex, ey = end
    angle = math.atan2(ey - sy, ex - sx)
    shaft_width = 14
    head_length = 34
    head_half = 15
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


def restore_base_from_git_history(display_code: str, base_path: Path) -> bool:
    rel_path = f"static/shelves/{display_code}.png"
    try:
        log_result = subprocess.run(
            ["git", "log", "--format=%H", "--", rel_path],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        commits = [line.strip() for line in log_result.stdout.splitlines() if line.strip()]
        if len(commits) < 2:
            return False
        for commit in commits[1:]:
            show_result = subprocess.run(
                ["git", "show", f"{commit}:{rel_path}"],
                cwd=ROOT,
                capture_output=True,
                check=False,
            )
            if show_result.returncode == 0 and show_result.stdout:
                base_path.write_bytes(show_result.stdout)
                return True
    except Exception:
        return False
    return False


def ensure_base_sources(display_codes: list[str]) -> None:
    BASES_DIR.mkdir(parents=True, exist_ok=True)
    for code in display_codes:
        public_path = SHELF_PICS_DIR / f"{code}.png"
        original_path = ORIGINALS_DIR / f"{code}.png"
        legacy_base_path = LEGACY_BASES_DIR / f"{code}.png"
        base_path = BASES_DIR / f"{code}.png"
        if base_path.exists():
            continue
        if legacy_base_path.exists():
            shutil.copy2(legacy_base_path, base_path)
            continue
        if public_path.exists() and original_path.exists():
            try:
                with Image.open(public_path) as public_img, Image.open(original_path) as original_img:
                    if public_img.width == original_img.width and public_img.height <= original_img.height + 4:
                        shutil.copy2(public_path, base_path)
                        continue
            except Exception:
                pass
        if restore_base_from_git_history(code, base_path):
            continue
        if public_path.exists() and original_path.exists():
            try:
                with Image.open(public_path) as public_img, Image.open(original_path) as original_img:
                    crop_h = min(public_img.height, original_img.height)
                    if crop_h > 0 and public_img.height > crop_h:
                        cropped = public_img.convert("RGBA").crop((0, 0, public_img.width, crop_h))
                        cropped.save(base_path)
                        continue
            except Exception:
                pass
        if original_path.exists():
            shutil.copy2(original_path, base_path)
        elif public_path.exists():
            shutil.copy2(public_path, base_path)


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
        fill=(255, 235, 59, 52),
        outline=(255, 205, 0, 255),
        width=10,
    )

    font = fit_font(label, rect)
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    text_x = rect[0] + ((rect[2] - rect[0] - text_w) // 2)
    text_y = rect[1] + ((rect[3] - rect[1] - text_h) // 2) - 3
    draw.text((text_x, text_y), label, fill=(33, 33, 33, 220), font=font)

    draw_arrow(draw, arrow_start, arrow_end)

    out_path = MAPS_DIR / f"officemap_{key}.png"
    base.save(out_path)
    return base


def main() -> None:
    for key, config in TARGETS.items():
        build_map_asset(key, config)

    print("Generated office map assets for", ", ".join(sorted(TARGETS)))
    print("Shelf/map combo images are no longer generated.")


if __name__ == "__main__":
    main()
