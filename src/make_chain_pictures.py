"""Make the signal-chain pictures: every 3D render (helix_model_icons/hxedit) and every NAM capture picture
(nam_capture_icons) at the size of HX Edit's chain block icon (38 x 38, the size of res/icons_category), the gear
cropped to its own outline and fitted inside a thin ring in its category's colour, on a transparent background.
HX Edit draws a chain block's frame grey and puts the category colour only in its flat glyph and label; the ring
keeps the colour at a glance once the glyph is replaced. NAM captures get the NAM categories' purple.

The colour of a stock picture comes from HX Edit's model catalog (the category that lists the model that shows
it); the few pictures no model in the catalog uses are matched by their render's category (icons.json).

Written to helix_model_icons/chain/<stock file name> and nam_capture_icons/chain/<id>.png, and the same with a grey
dimmed ring and picture for bypassed blocks in chain_off/, which nam_catalog.patch_chain_art copies into a prepared HX Edit as CHAIN_*.png
and CHAIN_OFF_*.png. Each picture sits on a soft dark tile and dark gear is brightened a little. Run again after the renders change:

    python make_chain_pictures.py [path to HX Edit's res/HX_ModelCatalog.json]     (needs Pillow; the editor does not)
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageStat

ROOT = Path(__file__).resolve().parent
CATALOG = Path(r'C:\Program Files (x86)\Line6\HX Edit\res\HX_ModelCatalog.json')
SIZE, FIT, RING, RADIUS = 38, 28, 2, 7     # the chain icon's size; the gear's longest side; the ring
NAM_PURPLE = 0xB070E0                      # nam_catalog.NAM_CATEGORY_COLOR
RING_OFF, GEAR_OFF = 0.42, 0.72            # a bypassed block (chain_off/): its ring and picture dimmed, as on the Floor
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


def chain_picture(source, target, colour, off=False):
    image = Image.open(source).convert('RGBA')
    box = image.getchannel('A').point(lambda alpha: 255 if alpha > 8 else 0).getbbox()
    if box:
        image = image.crop(box)
    image = lifted(image)
    if off:                                # dimmer still; HX Edit dims a bypassed block's picture as well
        alpha = image.getchannel('A')
        image = ImageEnhance.Brightness(image.convert('RGB')).enhance(GEAR_OFF).convert('RGBA')
        image.putalpha(alpha)
    scale = FIT / max(image.size)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    image = image.resize(size, Image.LANCZOS)
    # Drawn at 4x and reduced, so the ring's corners are smooth.
    big = Image.new('RGBA', (SIZE * 4, SIZE * 4), (0, 0, 0, 0))
    rgb = dimmed(colour, RING_OFF if off else 1.0) + (255,)
    ImageDraw.Draw(big).rounded_rectangle((2, 2, SIZE * 4 - 3, SIZE * 4 - 3), radius=RADIUS * 4, fill=TILE, outline=rgb,
                                          width=RING * 4)
    out = big.resize((SIZE, SIZE), Image.LANCZOS)
    out.alpha_composite(image, ((SIZE - size[0]) // 2, (SIZE - size[1]) // 2))
    out.save(target, optimize=True)


def main(argv):
    catalog = catalog_colours(Path(argv[0]) if argv else CATALOG)
    renders = json.loads((ROOT / 'helix_model_icons' / 'icons.json').read_text(encoding='utf-8'))['icons']
    made = 0
    models, captures = ROOT / 'helix_model_icons', ROOT / 'nam_capture_icons'
    for folder in (models / 'chain', models / 'chain_off', captures / 'chain', captures / 'chain_off'):
        folder.mkdir(exist_ok=True)
    for source in sorted((models / 'hxedit').glob('*.png')):
        chain_picture(source, models / 'chain' / source.name, colour_of(source.name, catalog, renders))
        chain_picture(source, models / 'chain_off' / source.name, colour_of(source.name, catalog, renders), off=True)
        made += 2
    for source in sorted(captures.glob('*.png')):
        chain_picture(source, captures / 'chain' / source.name, NAM_PURPLE)
        chain_picture(source, captures / 'chain_off' / source.name, NAM_PURPLE, off=True)
        made += 2
    print(f'{made} chain pictures')


if __name__ == '__main__':
    main(sys.argv[1:])
