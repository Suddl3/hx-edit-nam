"""Procedural 'plain' plates (no AI): pure black, and black with a soft floor glow. Pillow only."""
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFilter

PLATES = Path(__file__).resolve().parent / "plates"
W, H = 1536, 1024

Image.new("RGB", (W, H), (0, 0, 0)).save(PLATES / "black.png")

# soft floor glow: a wide, squashed ellipse of dim cool grey under the product spot, heavily blurred
g = Image.new("RGB", (W, H), (0, 0, 0))
d = ImageDraw.Draw(g)
cx, cy = W // 2, int(H * 0.66)
d.ellipse((cx - 560, cy - 150, cx + 560, cy + 150), fill=(58, 60, 66))
d.ellipse((cx - 330, cy - 85, cx + 330, cy + 85), fill=(92, 95, 102))
g = g.filter(ImageFilter.GaussianBlur(90))
# faint back wall wash above the floor
w = Image.new("RGB", (W, H), (0, 0, 0))
ImageDraw.Draw(w).ellipse((cx - 700, int(H * 0.05), cx + 700, int(H * 0.62)), fill=(20, 21, 25))
w = w.filter(ImageFilter.GaussianBlur(140))

ImageChops.lighter(g, w).save(PLATES / "soft_glow.png")
print("ok")
