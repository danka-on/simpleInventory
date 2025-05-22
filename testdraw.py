from PIL import Image, ImageDraw

def draw_and_resize():
    # Open the original image
    img = Image.open('static/gr1.png')
    draw = ImageDraw.Draw(img)
    # Draw a green rectangle at the specified coordinates
    rect_coords = (220, 163, 753, 407)
    draw.rectangle(rect_coords, outline=(0, 200, 0), width=8)
    # Resize to 480x640 (width x height)
    img_resized = img.resize((480, 640), Image.LANCZOS)
    # Save as gr1new.png in the same location
    img_resized.save('static/gr1new.png')

if __name__ == '__main__':
    draw_and_resize()
