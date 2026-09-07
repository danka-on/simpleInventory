"""Warehouse maps for Sweet Shelves."""

import io
import sqlite3
from PIL import Image, ImageDraw
from flask import jsonify, make_response, render_template, request, send_file, session


def load_image_efficiently(image_path, max_size=(1920, 1920), convert_rgb=True):
    """
    Load and resize image efficiently to prevent memory exhaustion on Pi.
    Uses thumbnail() to resize in-place without loading full image into memory.
    """
    try:
        img = Image.open(image_path)
        
        # Resize large images before converting (saves memory)
        if img.size[0] > max_size[0] or img.size[1] > max_size[1]:
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        # Convert to RGB if needed
        if convert_rgb and img.mode != 'RGB':
            img = img.convert('RGB')
        
        return img
    except Exception as e:
        raise Exception(f"Failed to load image {image_path}: {e}")


SHELF_COORDS = [
    {"gr1":
         {
        "s6":(220, 163, 753, 407),
        "s5": (90, 825, 930, 990),
        "s4":(90, 465, 930, 630),
        "s3":(90, 645, 930, 810),
        "s2":(90, 825, 930, 990),
        "s1":(90, 825, 930, 990)
         }
    },
    {"gr2":
    {
        "s6":(220, 163, 753, 407),
        "s5": (90, 825, 930, 990),
        "s4":(90, 465, 930, 630),
        "s3":(90, 645, 930, 810),
        "s2":(90, 825, 930, 990),
        "s1":(90, 825, 930, 990)
    }
    }
]


def highlight():
    shelf_name = request.args.get("shelf", "").lower().strip()
    print(f"[DEBUG] highlight() called with shelf_name: {shelf_name}")
    if not shelf_name or len(shelf_name) < 4 or not shelf_name.startswith("gr"):
        return "Invalid shelf parameter", 400
    rack = shelf_name[:3]  # e.g., 'gr1'
    shelf = shelf_name[3:]  # e.g., 's6'
    image_path = f"static/{rack}.png"
    # SHELF_COORDS is a list of dicts, find the dict for this rack
    coords = None
    for rack_dict in SHELF_COORDS:
        if rack in rack_dict:
            coords = rack_dict[rack].get(shelf)
            break
    if coords is None:
        return f"No coordinates found for {rack} {shelf}", 404
    try:
        # Use optimized image loading (memory-efficient for Pi)
        img = load_image_efficiently(image_path, max_size=(1920, 1920), convert_rgb=True)
    except Exception as e:
        return f"Image not found: {image_path}", 404
    draw = ImageDraw.Draw(img)
    draw.rectangle(coords, outline="green", width=30)
    img.thumbnail((180, 80))  # Substantially decrease image size
    img_io = io.BytesIO()
    img.save(img_io, "PNG")
    img_io.seek(0)
    return send_file(img_io, mimetype="image/png")


def show_inventory():
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()
    sort = request.args.get('sort', 'ID')
    dir = request.args.get('dir', 'asc')
    allowed_sorts = ['ID','Title','ItemID','SKU','Price','Quantity','List_State','Sold_Date','List_Date']
    if sort not in allowed_sorts:
        sort = 'ID'
    if dir not in ['asc','desc']:
        dir = 'asc'
    conn = sqlite3.connect("ebayStore.db")  # fixed typo here
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        sql = "SELECT * FROM INVENTORY WHERE 1=1"
        params = []
        if q:
            sql += " AND (Title LIKE ? OR ItemID LIKE ? OR SKU LIKE ? OR Price LIKE ? OR Quantity LIKE ? OR List_State LIKE ? OR Sold_Date LIKE ? OR List_Date LIKE ?)"
            for _ in range(8):
                params.append(f"%{q}%")
        if status:
            sql += " AND List_State = ?"
            params.append(status)
        sql += f" ORDER BY {sort} {dir.upper()}"
        cursor.execute(sql, params)
        items = cursor.fetchall()
    finally:
        conn.close()
    return render_template("inventory.html", items=items)


def upload_position_picture():
    import os
    from werkzeug.utils import secure_filename
    # Ensure static/pictureposition folder exists
    save_dir = os.path.join(os.getcwd(), 'static', 'pictureposition')
    os.makedirs(save_dir, exist_ok=True)
    
    file = request.files.get('picture')
    if not file:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    
    # Generate a unique filename with timestamp
    import time
    timestamp = int(time.time())
    filename = secure_filename(file.filename)
    name, ext = os.path.splitext(filename)
    unique_name = f"{timestamp}{ext}"
    
    # Save the file
    save_path = os.path.join(save_dir, unique_name)
    file.save(save_path)
    
    # Return just the filename (not full path) since it's in the static folder
    return jsonify({'success': True, 'path': unique_name})


def pictureposition_page():
    return render_template("pictureposition.html")


def set_pictureposition_path():
    data = request.get_json()
    session['inv_pictureposition_path'] = data.get('path')
    print(f"Set pictureposition_path from barcode.html: {session.get('inv_pictureposition_path')}")
    return jsonify({'success': True})


def searchrack_page():
    response = make_response(render_template('searchrack.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response
