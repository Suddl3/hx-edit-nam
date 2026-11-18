"""The Floor look file: what the Floor's picture tool (hx-unbound-floor, floor_emmc_art.py write/icons --input FILE) puts
in Focus View and the chain icons, chosen here instead of read from the editor's code. Format 1, agreed on
Suddl3/hx-unbound-floor#10:

  {"format": 1,
   "pictures": {"size": 256, "models_dir": <folder of HX Edit's picture file names, or left out: HX Edit's own>},
   "backgrounds": {"scenes_dir": <folder of <name>.png, 800 x 480>, "assign": {...}, "theme": {...}},
   "nam": {"preset_ids": [380], "slots": {"17": {"picture": <png>, "icon": <png>}}},
   "icons": {"size": 36}}

Paths are absolute; anything left out falls back to what the tool does without the file. nam.slots holds the User IR
slots (1..128) that play a NAM capture: each one's picture is the one the library shows for that capture (the user's
choice, else picked from its name), or picked from the slot's name for a capture the library doesn't keep.

  python floor_store.py --library DIR [--out FILE]     # offline: index the library, then write the file
"""
import argparse
import json
import os
from pathlib import Path
import re

import capture_icons

FORMAT = 1
HERE = Path(__file__).resolve().parent
NAM_PRESET_IDS = [380]             # the NAM A2 Lite block's preset id on the Floor firmware
PICTURE_SIZE, ICON_SIZE = 256, 36
ICON_MAX = 44                      # the chain icon is drawn inside the 48 x 48 block frame
FILE_NAME = 'floor-store.json'
MODELS_DIR = HERE / 'helix_model_icons' / 'hxedit'
SCENES_DIR = HERE / 'focus_backgrounds' / 'backgrounds'


# The Floor tool's default assignment (hx-unbound-floor helix-floor/floor/auto/assign_default.json, main 8cff2f3): what
# the page starts from. A category the Floor knows: HX Edit's category picture names, lower case.
DEFAULT_ASSIGN = {'default': 'studio', 'category': {
    'amp': 'stage', 'preamp': 'stage', 'distortion': 'studio', 'cab': 'stage', 'impulse response': 'stage',
    'dynamics': 'studio', 'eq': 'studio', 'modulation': 'aurora', 'delay': 'deep_sea', 'reverb': 'cloud_sunset',
    'filter': 'ice_cave', 'pitchsynth': 'space', 'wah': 'studio', 'volumepan': 'studio', 'input': 'foh_desk',
    'output': 'rack', 'sendreturn': 'patch_bay', 'split': 'road_cases', 'merge': 'black', 'looper': 'soft_glow'}}
CATEGORY_LABELS = {'amp': 'Amp', 'preamp': 'Preamp', 'distortion': 'Distortion', 'cab': 'Cab',
                   'impulse response': 'IR', 'dynamics': 'Dynamics', 'eq': 'EQ', 'modulation': 'Modulation',
                   'delay': 'Delay', 'reverb': 'Reverb', 'filter': 'Filter', 'pitchsynth': 'Pitch/Synth', 'wah': 'Wah',
                   'volumepan': 'Volume/Pan', 'input': 'Input', 'output': 'Output', 'sendreturn': 'Send/Return',
                   'split': 'Split', 'merge': 'Merge', 'looper': 'Looper'}
MANIFEST = HERE / 'focus_backgrounds' / 'focus_backgrounds.json'


def scenes(folder=None):
    """[{id, name, group, thumb}]: the backgrounds this editor has (manifest order), each with a small preview
    (focus_backgrounds/thumbs, a data: URI) when there is one. Empty when the editor has no backgrounds."""
    import base64
    folder = Path(folder) if folder else HERE / 'focus_backgrounds'
    try:
        styles = json.loads((folder / 'focus_backgrounds.json').read_text(encoding='utf-8')).get('styles') or []
    except (OSError, ValueError):
        return []
    out = []
    for style in styles:
        ident = Path(str(style.get('background') or '')).stem
        if not re.fullmatch(r'[a-z0-9_]{1,40}', ident) or not (folder / 'backgrounds' / f'{ident}.png').is_file():
            continue
        thumb = folder / 'thumbs' / f'{ident}.jpg'
        out.append({'id': ident, 'name': str(style.get('name') or ident), 'group': str(style.get('group') or ''),
                    'thumb': 'data:image/jpeg;base64,' + base64.b64encode(thumb.read_bytes()).decode('ascii')
                    if thumb.is_file() else ''})
    return out


def assignment(choice, known):
    """The file's backgrounds.assign from the page's choice {default, category: {name: scene}}: only scenes this editor
    has, only the Floor's categories; scenes = the ones used, each once (the Floor stores just those)."""
    choice = choice if isinstance(choice, dict) else {}
    known = set(known)
    default = choice.get('default') if choice.get('default') in known else DEFAULT_ASSIGN['default']
    category = {name: scene for name, scene in (choice.get('category') or {}).items()
                if name in CATEGORY_LABELS and scene in known} if isinstance(choice.get('category'), dict) else {}
    used = list(dict.fromkeys([default, *category.values()]))
    return {'scenes': used, 'default': default, 'category': category, 'preset': {}}


def view(slots, payloads, look=None, saved=None):
    """What the Floor look page shows: the capture slots (with the library capture behind each, if any), the
    backgrounds, the current assignment (the Floor tool's default until one is chosen) and the last save."""
    look = look if isinstance(look, dict) else {}
    rows = []
    for slot, record in sorted((slots or {}).items()):
        if type(slot) is not int or not 1 <= slot <= 128 or not isinstance(record, dict) or not record.get('contentId'):
            continue
        known = (payloads or {}).get(record['contentId'])
        known = known if isinstance(known, dict) else {}
        rows.append({'slot': slot, 'name': known.get('name') or str(record.get('name') or ''), 'id': known.get('id'),
                     'version': known.get('version'),
                     'icon': known.get('icon') or capture_icons.pick(str(record.get('name') or ''))})
    chosen = look.get('assign') if isinstance(look.get('assign'), dict) else None
    current = chosen or {'default': DEFAULT_ASSIGN['default'], 'category': dict(DEFAULT_ASSIGN['category'])}
    return {'slots': rows, 'scenes': scenes(), 'assign': {'default': current.get('default'),
                                                         'category': dict(current.get('category') or {})},
            'chosen': chosen is not None, 'categories': [[key, label] for key, label in CATEGORY_LABELS.items()],
            'gear': category_gear(), 'saved': saved,
            'defaults': {'default': DEFAULT_ASSIGN['default'], 'category': dict(DEFAULT_ASSIGN['category'])}}


def category_gear(folder=None):
    """{category or 'default': data: URI}: the one piece of gear the page shows over each kind of block's background
    (focus_backgrounds/category_gear, make_page_pictures.py), so picking a background changes only the background."""
    import base64
    folder = Path(folder) if folder else HERE / 'focus_backgrounds' / 'category_gear'
    out = {}
    for key in ('default', *CATEGORY_LABELS):
        path = folder / f'{key}.png'
        if path.is_file():
            out[key] = 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')
    return out


def _folder(value):
    path = Path(value) if isinstance(value, (str, Path)) and str(value) else None
    return path.resolve() if path is not None and path.is_dir() else None


def picture_file(ident, pictures=None, art_dirs=()):
    """The PNG of a picture id: an uploaded one (u_...) from the library's pictures folder, else the first art folder
    that has it (a bigger set, e.g. the 256 px gear art), else the editor's own. None when there is no file."""
    ident = str(ident or '')
    if capture_icons.UPLOADED.fullmatch(ident):
        path = Path(pictures) / f'{ident}.png' if pictures else None
        return path.resolve() if path is not None and path.is_file() else None
    if not re.fullmatch(r'[a-z0-9_]{1,40}', ident):
        return None
    for folder in [*art_dirs, capture_icons.FOLDER]:
        path = Path(folder) / f'{ident}.png'
        if path.is_file():
            return path.resolve()
    return None


def slot_pictures(slots, payloads, pictures=None, art_dirs=()):
    """{slot: {'picture': path, 'icon': path}} for the capture slots that hold a NAM capture.

    slots: the Floor's User IR slots as the editor remembers them ({slot: {'name', 'contentId'}}; a contentId only for
    a NAM payload). payloads: library.payload_metadata() ({contentId: {'icon', ...}}): the library's picture for it."""
    out = {}
    for slot, record in sorted((slots or {}).items()):
        if type(slot) is not int or not 1 <= slot <= 128 or not isinstance(record, dict) or not record.get('contentId'):
            continue
        known = (payloads or {}).get(record['contentId'])
        ident = known.get('icon') if isinstance(known, dict) else None
        path = picture_file(ident, pictures, art_dirs) if ident else None
        if path is None:
            path = picture_file(capture_icons.pick(str(record.get('name') or '')), pictures, art_dirs)
        if path is not None:
            out[slot] = {'picture': str(path), 'icon': str(path)}
    return out


def build(slots, payloads, *, pictures=None, look=None):
    """The file's contents. look: settings.json "floorLook" (all optional): {"gearArt": folder of bigger capture
    pictures, "pictureSize", "iconSize", "modelsDir", "scenesDir", "assign", "theme"}."""
    look = look if isinstance(look, dict) else {}
    art = [folder for folder in [_folder(look.get('gearArt'))] if folder]
    size = look.get('pictureSize', PICTURE_SIZE)
    icon = look.get('iconSize', ICON_SIZE)
    if type(size) is not int or not 16 <= size <= 256:
        raise ValueError(f'Picture size {size!r}: 16..256')
    if type(icon) is not int or not 1 <= icon <= ICON_MAX:
        raise ValueError(f'Icon size {icon!r}: 1..{ICON_MAX}')
    store = {'format': FORMAT, 'pictures': {'size': size}}
    models = _folder(look.get('modelsDir')) or _folder(MODELS_DIR)
    if models:
        store['pictures']['models_dir'] = str(models)
    scenes = _folder(look.get('scenesDir')) or _folder(SCENES_DIR)
    if scenes:
        store['backgrounds'] = {'scenes_dir': str(scenes)}
        for key in ('assign', 'theme'):
            if isinstance(look.get(key), dict):
                store['backgrounds'][key] = look[key]
    store['nam'] = {'preset_ids': list(NAM_PRESET_IDS),
                    'slots': {str(slot): value for slot, value in slot_pictures(slots, payloads, pictures, art).items()}}
    store['icons'] = {'size': icon}
    return store


def write(path, store):
    """Replace the file in one step (a reader never sees half of it)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.stem}.{os.getpid()}.tmp')
    try:
        temporary.write_text(json.dumps(store, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def write_for(library, memory, look=None, path=None):
    """Write the file next to the library from a running editor's library (its payload index) and Floor memory."""
    slots = {slot: {'name': name, 'contentId': content} for slot, name, content in memory.slots()}
    store = build(slots, library.payload_metadata(), pictures=library.pictures, look=look)
    return write(path or Path(library.folder) / FILE_NAME, store), store


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--library', required=True, help="the editor's NAM library folder (catalog.json, floor-memory.json)")
    ap.add_argument('--out', default='')
    ap.add_argument('--gear-art', default='', help='a folder of bigger capture pictures (overrides floorLook.gearArt)')
    ap.add_argument('--converter', default=str(HERE.parent.parent / 'helix-lt-nam' / 'tools' / 'nam2ir.py'),
                    help="helix-lt-nam's tools/nam2ir.py, to match .nam captures to the Floor's slots")
    a = ap.parse_args(argv)
    import tempfile
    from floor_inventory import prime_library
    from floor_memory import FloorMemory
    from nam_catalog import load_settings
    from nam_library import Library
    library = Library(Path(a.library))
    with tempfile.TemporaryDirectory() as workspace:
        indexed = prime_library(library, Path(a.converter), workspace)
    look = dict(load_settings().get('floorLook') or {})
    if a.gear_art:
        look['gearArt'] = a.gear_art
    path, store = write_for(library, FloorMemory(library.folder), look, a.out or None)
    print(f"{len(store['nam']['slots'])} capture slots with pictures ({indexed['indexed']} library captures indexed, "
          f"{len(indexed['unsupported'])} not): {path}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
