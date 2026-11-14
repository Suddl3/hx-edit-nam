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


# Model lock buttons (nam_catalog.patch_model_lock): 16x16 padlocks, four frames stacked like
# HX Edit's own push buttons (normal, disabled, pressed, hover; measured from the stock strips).
LOCK_FRAMES = ((0xD0, 255), (0x50, 255), (0xA0, 255), (0xFF, 255))


def padlock(locked, grey, alpha):
    size, scale = 16, 8
    image = Image.new('RGBA', (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    colour, width = (grey, grey, grey, alpha), int(1.6 * scale)
    z = lambda *values: [value * scale for value in values]
    draw.rounded_rectangle(z(3, 7.5, 13, 15), radius=1.5 * scale, fill=colour)          # body
    if locked:
        draw.arc(z(5, 2, 11, 10), 180, 360, fill=colour, width=width)                     # closed shackle
        draw.line(z(5.8, 6, 5.8, 8), fill=colour, width=width)
        draw.line(z(10.2, 6, 10.2, 8), fill=colour, width=width)
    else:
        draw.arc(z(7.5, 0.5, 13.5, 8.5), 180, 360, fill=colour, width=width)              # open, lifted right
        draw.line(z(12.7, 4.5, 12.7, 8), fill=colour, width=width)
        draw.line(z(8.3, 4.5, 8.3, 5.5), fill=colour, width=width)
    draw.ellipse(z(7, 9.8, 9, 11.8), fill=(0, 0, 0, 0))                                 # keyhole
    draw.rectangle(z(7.6, 11, 8.4, 13), fill=(0, 0, 0, 0))
    return image.resize((size, size), Image.LANCZOS)


def lock_buttons():
    for locked, name in ((True, 'model_locked.png'), (False, 'model_unlocked.png')):
        strip = Image.new('RGBA', (16, 64), (0, 0, 0, 0))
        for index, (grey, alpha) in enumerate(LOCK_FRAMES):
            strip.alpha_composite(padlock(locked, grey, alpha), (0, 16 * index))
        OUT.mkdir(exist_ok=True)
        strip.save(OUT / name)


if __name__ == '__main__':
    for draw_icon in (amp, amp_cab, pedal, other, section, lock_buttons):
        draw_icon()
    print(sorted(path.name for path in OUT.glob('*.png')))
