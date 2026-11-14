"""Gear icons for NAM captures, picked from the capture's name.

nam_capture_icons/ holds one 128 px picture per look (and a 48 px copy in 48/ for the library page) (angled product renders without logos or text)
and icons.json, the rules: each icon's group (amp, rig, cab, pedal, other) and the name keywords
that select it. The longest matching keyword wins, weak words (boost, reverb, cab...) count little,
and a capture category rules out icons of another kind (a pedal never gets an amp head). Cab or
IR captures (and unsorted ones) whose names mention a cab (4x12, cab, IR, speaker) prefer cab icons. A name that matches nothing gets one
of its group's generic icons, chosen from a hash of the name so it never changes. A picture the
user chose in the library window (the entry's "icon") wins over all of this: one of these, or a
picture the user uploaded (u_<16 hex>, kept in the library's pictures folder).
"""
import base64
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

FOLDER = Path(__file__).with_name('nam_capture_icons')
# Library gear names -> icon groups. None: unknown, so every kind of icon may match.
GEAR_GROUPS = {'amp': 'amp', 'preamp': 'amp', 'amp-cab': 'rig', 'amp-pedal-cab': 'rig',
               'pedal': 'pedal', 'pedal-amp': 'pedal', 'cab': 'other', 'studio': 'other',
               'outboard': 'other', 'experimental': 'other', 'space': 'other'}
ALLOWED = {'pedal': {'pedal'}, 'amp': {'amp', 'rig', 'cab', 'other'}, 'rig': {'amp', 'rig', 'cab', 'other'},
           'other': {'amp', 'rig', 'cab', 'pedal', 'other'}}
UPLOADED = re.compile(r'u_[0-9a-f]{16}')
CAB_CUE = re.compile(r'(?<![a-z0-9])(\dx\d\d?|cab|cabinet|ir|irs|impulse|speaker)(?![a-z])')


def _words(text, camel=True):
    if camel:
        text = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', text)      # "WhoWatt" -> "Who Watt"
    return ' ' + re.sub(r'[^a-z0-9.+]+', ' ', text.lower()) + ' '


@lru_cache(maxsize=1)
def rules():
    data = json.loads((FOLDER / 'icons.json').read_text(encoding='utf-8'))
    weak = set(data['weak'])
    compiled = []
    for icon in data['icons']:
        for keyword in icon['keywords']:
            text = re.escape(_words(keyword).strip())
            tail = r'(?![0-9])' if keyword[-1].isdigit() else r'(?![a-z])'
            compiled.append((icon['id'], icon['group'], keyword, re.compile(r'(?<![a-z0-9])' + text + tail),
                             1 if keyword in weak else len(keyword)))
    return compiled, data['generic']


def pick(name, gear=None):
    """Icon id for a capture name; gear is the library's gear name when known."""
    compiled, generic = rules()
    name = str(name or '')
    group = GEAR_GROUPS.get(gear or '')
    texts = (_words(name), _words(name, camel=False))
    cab = any(CAB_CUE.search(text) for text in texts)
    allowed = ALLOWED[group or 'other']
    best = None
    for ident, kind, _, pattern, score in compiled:
        if kind not in allowed or not any(pattern.search(text) for text in texts):
            continue
        if kind == 'cab':
            # "Marshall 4x12 IR" is the cab, not the head; but an amp or amp + cab capture named
            # "... V30 CAB" is the amp.
            score += 10 if cab and group in (None, 'other') else -3
        if best is None or score > best[1]:
            best = (ident, score)
    if best:
        return best[0]
    pool = generic[group or 'other']
    return pool[int(hashlib.sha1(name.encode('utf-8')).hexdigest(), 16) % len(pool)]


@lru_cache(maxsize=1)
def known_icons():
    """Every picture id, in icons.json order."""
    return tuple(icon['id'] for icon in json.loads((FOLDER / 'icons.json').read_text(encoding='utf-8'))['icons'])


def icon_for(entry, pictures=None):
    """The library entry's picture: the user's choice (an uploaded one only while its file is in
    the pictures folder), else the one picked from its name."""
    chosen = entry.get('icon')
    if chosen in known_icons():
        return chosen
    if pictures and UPLOADED.fullmatch(str(chosen or '')) and (Path(pictures) / f'{chosen}.png').is_file():
        return chosen
    return pick(entry.get('name', ''), entry.get('gear') or None)


PICKER_GROUPS = (('amp', 'Amps'), ('rig', 'Amp + cab'), ('cab', 'Cabs'), ('pedal', 'Pedals'), ('other', 'Other gear'))


@lru_cache(maxsize=1)
def picker_groups():
    """[{label, ids}] for the library window's picture picker."""
    icons = json.loads((FOLDER / 'icons.json').read_text(encoding='utf-8'))['icons']
    return [dict(label=label, ids=[icon['id'] for icon in icons if icon['group'] == group])
            for group, label in PICKER_GROUPS]


def icon_path(name, gear=None, ident=None):
    path = FOLDER / f'{ident or pick(name, gear)}.png'
    return str(path) if path.is_file() else ''


@lru_cache(maxsize=256)
def small_data_uri(ident):
    """The 48 px picture as a data: URI for the library page ('' when there is none)."""
    if not re.fullmatch(r'[a-z0-9_]{1,40}', ident or ''):
        return ''
    path = FOLDER / '48' / f'{ident}.png'
    return 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode('ascii') if path.is_file() else ''
