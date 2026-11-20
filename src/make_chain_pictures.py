"""Make the signal-chain pictures: every 3D render (helix_model_icons/hxedit) and every NAM capture picture
(nam_capture_icons) at the size of HX Edit's chain block icon (38 x 38, the size of res/icons_category), the gear
cropped to its own outline and fitted inside a thin ring in its category's colour, on a transparent background.
HX Edit draws a chain block's frame grey and puts the category colour only in its flat glyph and label; the ring
keeps the colour at a glance once the glyph is replaced. NAM captures get the NAM categories' purple.

The colour of a stock picture comes from HX Edit's model catalog (the category that lists the model that shows
it); the few pictures no model in the catalog uses are matched by their render's category (icons.json).

The large set (chain_large/, chain_large_off/: 85 x 70, the whole height of a chain block and wider) is for "Large
pictures in the signal chain": no tile and no ring. A block that is on has a soft glow in its category's colour
behind the gear; a bypassed one has none, so on and off read at a glance. HX Edit draws the block's label over the
picture's bottom edge, so the picture is shaded there.

Written to helix_model_icons/chain/<stock file name> and nam_capture_icons/chain/<id>.png, and the same with a grey
dimmed ring and picture for bypassed blocks in chain_off/, which nam_catalog.patch_chain_art copies into a prepared HX Edit as CHAIN_*.png
and CHAIN_OFF_*.png. Each picture sits on a soft dark tile and dark gear is brightened a little. Run again after the renders change:

    python make_chain_pictures.py [path to HX Edit's res/HX_ModelCatalog.json]     (needs Pillow; the editor does not)
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageStat

ROOT = Path(__file__).resolve().parent
CATALOG = Path(r'C:\Program Files (x86)\Line6\HX Edit\res\HX_ModelCatalog.json')
SIZE, FIT, RING, RADIUS = 38, 28, 2, 7     # the chain icon's size; the gear's longest side; the ring
# "Large pictures in the signal chain" (chain_large/): the picture box tone_view gives a block (LARGE_BOX there,
# 68 x 56 in HX Edit's units) at the 1.25 the stock pictures are drawn at; the gear's largest size inside it.
LARGE, LARGE_FIT = (85, 70), (74, 58)
GLOW_SPREAD, GLOW_BLUR, GLOW_GAIN = 1, 8, 1.0   # the glow: barely grown, widely blurred: a haze, not an outline
LIFT = 2                                   # px the gear sits above the middle, clear of the label
LABEL_BAND, LABEL_SHADE = 16, 0.3          # the bottom rows HX Edit writes the label over; how dark they end
NAM_PURPLE = 0xB070E0                      # nam_catalog.NAM_CATEGORY_COLOR
RING_OFF, GEAR_OFF = 0.8, 0.8               # a bypassed block (chain_off/), dimmed as on the Floor; HX Edit
                                           # also draws it at 0.66 opacity: about 0.5 of the colour in all
TILE = (44, 44, 50, 235)                   # behind the gear, so dark amps and pedals stand off the black chain
# A render's own category (icons.json) -> the colour of HX Edit's category of that name.
RENDER_COLOURS = {'amp': 0xDD1111, 'preamp': 0xDD1111, 'cab': 0xDD1111, 'distortion': 0xF5901E,
                  'dynamics': 0xDDCC00, 'eq': 0xDDCC00, 'modulation': 0x0094E9, 'delay': 0x00CC00,
                  'reverb': 0xFF5C00, 'pitch': 0xAD46E2, 'filter': 0xAD46E2, 'wah': 0xA844DB, 'ir': 0xF23091,
                  'volume': 0x38A696, 'send/return': 0x38A696, 'looper': 0x989898, 'io': 0x989898,
                  'routing': 0x989898, 'other': 0x989898}
# Pictures no catalog model uses: by the file name's category word.
NAME_COLOURS = (('REVERB', 0xFF5C00), ('DELAY', 0x00CC00), ('MOD', 0x0094E9), ('DIST', 0xF5901E),
                ('DYN', 0xDDCC00), ('COMP', 0xDDCC00), ('PITCH', 0xAD46E2), ('FILTER', 0xAD46E2), ('WAH', 0xA844DB),
                ('AMP', 0xDD1111), ('CAB', 0xDD1111), ('EQ', 0xDDCC00), ('VOL', 0x38A696), ('LOOP', 0x989898))


def catalog_colours(path):
    """{picture file: category colour} from HX Edit's model catalog."""
    colours = {}

    def walk(node, colour=None):
        if isinstance(node, dict):
            if 'color' in node and 'name' in node:
                colour = int(str(node['color']), 16)
            image = node.get('image')
            if isinstance(image, str) and image.endswith('.png') and not image.startswith('FX_HX_Category'):
                colours.setdefault(image, colour)
            for value in node.values():
                walk(value, colour)
        elif isinstance(node, list):
            for value in node:
                walk(value, colour)
    walk(json.loads(Path(path).read_text(encoding='utf-8')))
    return colours


def colour_of(name, catalog, renders):
    if catalog.get(name) is not None:
        return catalog[name]
    render = renders.get(name, {}).get('category')
    if render in RENDER_COLOURS and render not in ('other', 'legacy'):
        return RENDER_COLOURS[render]
    upper = name.upper()
    return next((colour for word, colour in NAME_COLOURS if f'_{word}' in upper), 0x989898)


def lifted(image):
    """Dark gear brightened a little (by how dark it is), so it reads on the chain's black."""
    mask = image.getchannel('A').point(lambda alpha: 255 if alpha > 128 else 0)
    if not mask.getbbox():
        return image
    light = ImageStat.Stat(image.convert('L'), mask).mean[0]
    factor = 1.45 if light < 55 else 1.25 if light < 80 else 1.1 if light < 100 else 1.0
    if factor == 1.0:
        return image
    alpha = image.getchannel('A')
    image = ImageEnhance.Brightness(image.convert('RGB')).enhance(factor).convert('RGBA')
    image.putalpha(alpha)
    return image


def dimmed(colour, factor):
    return tuple(round(((colour >> shift) & 255) * factor) for shift in (16, 8, 0))


def gear_image(source, off):
    image = Image.open(source).convert('RGBA')
    box = image.getchannel('A').point(lambda alpha: 255 if alpha > 8 else 0).getbbox()
    if box:
        image = image.crop(box)
    image = lifted(image)
    if off:                                # dimmer still; HX Edit dims a bypassed block's picture as well
        alpha = image.getchannel('A')
        image = ImageEnhance.Brightness(image.convert('RGB')).enhance(GEAR_OFF).convert('RGBA')
        image.putalpha(alpha)
    return image


def chain_picture(source, target, colour, off=False):
    image = gear_image(source, off)
    scale = FIT / max(image.size)
    gear = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    image = image.resize(gear, Image.LANCZOS)
    # Drawn at 4x and reduced, so the ring's corners are smooth.
    big = Image.new('RGBA', (SIZE * 4, SIZE * 4), (0, 0, 0, 0))
    rgb = dimmed(colour, RING_OFF if off else 1.0) + (255,)
    ImageDraw.Draw(big).rounded_rectangle((2, 2, SIZE * 4 - 3, SIZE * 4 - 3), radius=RADIUS * 4, fill=TILE, outline=rgb, width=RING * 4)
    out = big.resize((SIZE, SIZE), Image.LANCZOS)
    out.alpha_composite(image, ((SIZE - gear[0]) // 2, (SIZE - gear[1]) // 2))
    out.save(target, optimize=True)


def large_picture(source, target, colour, off=False):
    """The gear alone, centred (the chain's cable runs through the picture's middle); on: a glow in the colour."""
    image = gear_image(source, off)
    scale = min(LARGE_FIT[0] / image.width, LARGE_FIT[1] / image.height)
    gear = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    image = image.resize(gear, Image.LANCZOS)
    width, height = LARGE
    place = ((width - gear[0]) // 2, max(0, (height - gear[1]) // 2 - LIFT))
    out = Image.new('RGBA', LARGE, (0, 0, 0, 0))
    if not off:
        shape = Image.new('L', LARGE, 0)
        shape.paste(image.getchannel('A'), place)
        glow = shape.filter(ImageFilter.MaxFilter(2 * GLOW_SPREAD + 1)).filter(ImageFilter.GaussianBlur(GLOW_BLUR))
        glow = glow.point(lambda alpha: min(255, round(alpha * GLOW_GAIN)))
        # Nothing at the picture's edges, so the glow never ends in a straight line.
        edge = Image.new('L', LARGE, 0)
        ImageDraw.Draw(edge).rectangle((3, 3, width - 4, height - 4), fill=255)
        glow = ImageChops.multiply(glow, edge.filter(ImageFilter.GaussianBlur(3)))
        out = Image.merge('RGBA', (*Image.new('RGB', LARGE, dimmed(colour, 1.0)).split(), glow))
    out.alpha_composite(image, place)
    # The label's rows: darker towards the bottom, so the label reads on the gear and on the glow.
    shade = Image.new('L', LARGE, 255)
    for row in range(LABEL_BAND):
        level = 1 - (1 - LABEL_SHADE) * min(1.0, (row + 1) / (LABEL_BAND * 0.6))
        ImageDraw.Draw(shade).line((0, height - LABEL_BAND + row, width, height - LABEL_BAND + row), fill=round(255 * level))
    red, green, blue, alpha = out.split()
    out = Image.merge('RGBA', (*(ImageChops.multiply(band, shade) for band in (red, green, blue)), alpha))
    out.save(target, optimize=True)


def main(argv):
    catalog = catalog_colours(Path(argv[0]) if argv else CATALOG)
    renders = json.loads((ROOT / 'helix_model_icons' / 'icons.json').read_text(encoding='utf-8'))['icons']
    made = 0
    models, captures = ROOT / 'helix_model_icons', ROOT / 'nam_capture_icons'
    sets = (('chain', chain_picture), ('chain_large', large_picture))
    for folder, _ in sets:
        for root in (models, captures):
            (root / folder).mkdir(exist_ok=True)
            (root / f'{folder}_off').mkdir(exist_ok=True)
    jobs = [(source, models, colour_of(source.name, catalog, renders)) for source in sorted((models / 'hxedit').glob('*.png'))]
    jobs += [(source, captures, NAM_PURPLE) for source in sorted(captures.glob('*.png'))]
    for source, root, colour in jobs:
        for folder, make in sets:
            make(source, root / folder / source.name, colour)
            make(source, root / f'{folder}_off' / source.name, colour, off=True)
            made += 2
    print(f'{made} chain pictures')


if __name__ == '__main__':
    main(sys.argv[1:])
