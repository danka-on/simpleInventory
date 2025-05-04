import io

from flask import request, send_file, url_for
from PIL import Image, ImageDraw

# Coordinates of each shelf rectangle (left, top, right, bottom)
SHELF_COORDS = {
    "shelf top": (58, 89, 647, 246),
    "shelf 4":   (58, 253, 647, 409),
    "shelf 3":   (58, 417, 647, 573),
    "shelf 2":   (58, 582, 647, 738),
    "shelf 1":   (58, 747, 647, 902),
}

def highLight():
    shelf_name = request.args.get("shelf", "").lower()

    # Load original image
    image_path = "static/shelves_base.png"  # move your base image here
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Draw the selected shelf in green
    if shelf_name in SHELF_COORDS:
        draw.rectangle(SHELF_COORDS[shelf_name], outline="green", width=6)

    # Output to memory, not file
    img_io = io.BytesIO()
    img.save(img_io, "PNG")
    img_io.seek(0)
    return send_file(img_io, mimetype="image/png")