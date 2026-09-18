"""Small cached copies of photos under static/ for list thumbnails.

Phone photos saved by the lister and Prep + (static/custom_items) are
3000-4000px and 2-4 MB; a 104px thumbnail does not need that. The first
request resizes once and caches a JPEG keyed on path, width and mtime.
"""
import hashlib
import io
import uuid

from flask import abort, request, send_file
from PIL import Image, ImageOps

from . import config as ss_config

STATIC_DIR = (ss_config.BASE_DIR / 'static').resolve()
CACHE_DIR = ss_config.BASE_DIR / 'cache' / 'static_thumbs'
WIDTHS = (120, 240, 480, 640)
IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}


def _thumb_width(value):
    try:
        wanted = int(value)
    except (TypeError, ValueError):
        return 240
    return next((w for w in WIDTHS if w >= wanted), WIDTHS[-1])


def _source_path(filename):
    source = (STATIC_DIR / filename).resolve()
    if STATIC_DIR not in source.parents or source.suffix.lower() not in IMAGE_SUFFIXES:
        return None
    return source if source.is_file() else None


def _render(source, width):
    with Image.open(source) as img:
        img.draft('RGB', (width, width))  # JPEG decodes at reduced scale: cheap on the Pi
        img = ImageOps.exif_transpose(img)
        img.thumbnail((width, width), Image.Resampling.LANCZOS)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        out = io.BytesIO()
        img.save(out, 'JPEG', quality=82, optimize=True)
        return out.getvalue()


def static_thumb(filename):
    source = _source_path(filename)
    if source is None:
        abort(404)
    width = _thumb_width(request.args.get('w'))
    stat = source.stat()
    key = hashlib.sha1(f'{source}|{width}|{stat.st_mtime_ns}|{stat.st_size}'.encode()).hexdigest()
    cached = CACHE_DIR / f'{key}.jpg'
    if not cached.is_file():
        try:
            data = _render(source, width)
        except Exception:
            # Not something Pillow can shrink: hand back the original.
            return send_file(source, max_age=86400)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_DIR / f'{key}.{uuid.uuid4().hex}.tmp'  # two threads may render the same photo
        tmp.write_bytes(data)
        tmp.replace(cached)
    return send_file(cached, mimetype='image/jpeg', max_age=86400)
