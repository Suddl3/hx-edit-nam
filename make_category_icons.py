"""Draw the NAM block-category icons (development tool; needs Pillow).

HX Edit's category icons are 38x38 line drawings in the category's colour on a transparent
background. These are drawn from primitives, not derived from the stock icons, and written to
nam_category_icons/. nam_catalog.patch_nam_category copies them into a prepared copy of HX Edit.

    python make_category_icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

SIZE, SCALE = 38, 8
COLOR = (176, 112, 224, 255)          # 0xB070E0, the NAM categories' colour
OUT = Path(__file__).with_name('nam_category_icons')


def canvas():
    image = Image.new('RGBA', (SIZE * SCALE, SIZE * SCALE), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image)


def s(*values):
    return [value * SCALE for value in values]


def finish(image, name):
    OUT.mkdir(exist_ok=True)
    image.resize((SIZE, SIZE), Image.LANCZOS).save(OUT / name)


WIDTH = int(1.6 * SCALE)


def amp():
    image, draw = canvas()
    draw.arc(s(13, 6, 25, 16), 180, 360, fill=COLOR, width=WIDTH)                 # handle
    draw.rounded_rectangle(s(4, 11, 34, 29), radius=2 * SCALE, outline=COLOR, width=WIDTH)
    for x in (11, 19, 27):                                                        # three knobs
        draw.ellipse(s(x - 2.2, 17.8, x + 2.2, 22.2), outline=COLOR, width=WIDTH)
    draw.line(s(8, 29, 8, 32), fill=COLOR, width=WIDTH)
    draw.line(s(30, 29, 30, 32), fill=COLOR, width=WIDTH)
    finish(image, 'FX_HX_Category_NAM_Amp.png')


def amp_cab():
    image, draw = canvas()
    draw.rounded_rectangle(s(8, 4, 30, 12), radius=1.5 * SCALE, outline=COLOR, width=WIDTH)   # head
    draw.rounded_rectangle(s(5, 14, 33, 34), radius=2 * SCALE, outline=COLOR, width=WIDTH)    # cabinet
    draw.ellipse(s(13, 18, 25, 30), outline=COLOR, width=WIDTH)                                # speaker
    finish(image, 'FX_HX_Category_NAM_AmpCab.png')


def pedal():
    image, draw = canvas()
    draw.rounded_rectangle(s(10, 4, 28, 34), radius=3 * SCALE, outline=COLOR, width=WIDTH)
    draw.ellipse(s(15.5, 8.5, 22.5, 15.5), outline=COLOR, width=WIDTH)            # knob
    draw.line(s(13, 20, 25, 20), fill=COLOR, width=WIDTH)
    draw.ellipse(s(16, 24, 22, 30), fill=COLOR)                                   # footswitch
    finish(image, 'FX_HX_Category_NAM_Pedal.png')


def other():
    image, draw = canvas()
    points = [(4, 19), (9, 19), (12, 9), (17, 29), (21, 13), (25, 24), (28, 19), (34, 19)]
    draw.line([coordinate * SCALE for point in points for coordinate in point], fill=COLOR, width=WIDTH, joint='curve')
    finish(image, 'FX_HX_Category_NAM_Other.png')


def section():
    image, _ = canvas()                                                           # nothing: a heading row
    finish(image, 'FX_HX_Category_Section.png')


if __name__ == '__main__':
    for draw_icon in (amp, amp_cab, pedal, other, section):
        draw_icon()
    print(sorted(path.name for path in OUT.glob('*.png')))
