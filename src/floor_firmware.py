"""Which HX Unbound Floor firmware the editor is set up for: one place for what used to be scattered flags.

Firmware built with NAM_UNBOUND_INFO=1 answers editor command 0x53 with its NAM blocks and costs (from_identity).
Otherwise the profile comes from, first to last:
  - HX_EDIT_FLOOR_FIRMWARE: JSON, e.g. {"namCost": 3250, "pedal": true} (builds and tests)
  - settings.json "floorFirmware": the same JSON object
  - the defaults: the standard image (NAM A2 Lite at cost 3600, no NAM Pedal block)

HX Edit sums each block's load itself (load = the firmware's cost / 100), so the catalog must carry the
firmware's own costs, or HX Edit greys out blocks that would fit (a PAIR build runs NAM at 3250).

A prepared HX Edit copy records the profile its catalog was built with in res/floor-firmware.json, so what
runs (pedal block offered, pedal files accepted) always matches the catalog HX Edit was given.
"""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path

RECORD = 'floor-firmware.json'
NAM_COSTS = (2000, 5000)       # sane bounds for a declared NAM block cost (the Floor's budget is 9000 a DSP)
PEDAL_COSTS = (300, 3000)


@dataclass(frozen=True)
class FloorFirmware:
    nam_cost: int = 3600       # HD2_AmpNAMLite, id 380
    pedal: bool = False        # HD2_AmpNAMPedal, id 768 (NAM_PEDAL=1 builds)
    pedal_cost: int = 850      # the LT's measured value until the Floor's is measured
    source: str = 'default'

    @property
    def nam_load(self):
        return self.nam_cost / 100

    @property
    def pedal_load(self):
        return self.pedal_cost / 100

    def describe(self):
        text = f'NAM A2 Lite {self.nam_load:g}%'
        return text + (f' + NAM Pedal {self.pedal_load:g}%' if self.pedal else '')


def _cost(value, bounds, name):
    if isinstance(value, bool) or not isinstance(value, int) or not bounds[0] <= value <= bounds[1]:
        raise ValueError(f'Floor firmware: {name} must be a whole number from {bounds[0]} to {bounds[1]}.')
    return value


def from_dict(data, source):
    if not isinstance(data, dict):
        raise ValueError('Floor firmware settings must be a JSON object.')
    unknown = set(data) - {'namCost', 'pedal', 'pedalCost'}
    if unknown:
        raise ValueError('Floor firmware: unknown setting ' + ', '.join(sorted(unknown)) + '.')
    pedal = data.get('pedal', False)
    if not isinstance(pedal, bool):
        raise ValueError('Floor firmware: pedal must be true or false.')
    return FloorFirmware(nam_cost=_cost(data.get('namCost', 3600), NAM_COSTS, 'namCost'), pedal=pedal,
                         pedal_cost=_cost(data.get('pedalCost', 850), PEDAL_COSTS, 'pedalCost'), source=source)


def profile(settings=None):
    """The configured profile (see the module text). settings: settings.json's contents (read when None)."""
    if text := os.environ.get('HX_EDIT_FLOOR_FIRMWARE'):
        try:
            data = json.loads(text)
        except ValueError:
            raise ValueError('HX_EDIT_FLOOR_FIRMWARE is not valid JSON.') from None
        return from_dict(data, 'environment')
    if settings is None:
        from nam_catalog import load_settings
        settings = load_settings()
    if 'floorFirmware' in settings:
        return from_dict(settings['floorFirmware'], 'settings')
    return FloorFirmware()


NAM_ID, PEDAL_ID = 380, 768


def from_identity(reply):
    """The profile from the Floor's own answer to editor command 0x53 (hx-unbound-floor unbound_info.py, format 1):
    {"fmt": 1, "blocks": [[preset id, DSP cost], ...], "feat": [...]}. None for a reply this editor does not understand
    (a newer format, no NAM block): the caller then keeps the configured profile."""
    if not isinstance(reply, dict) or reply.get('fmt') != 1 or not isinstance(reply.get('blocks'), list):
        return None
    costs = {}
    for block in reply['blocks']:
        if isinstance(block, list) and len(block) == 2 and all(isinstance(v, int) for v in block):
            costs[block[0]] = block[1]
    if NAM_ID not in costs:
        return None
    try:
        data = {'namCost': costs[NAM_ID], 'pedal': PEDAL_ID in costs}
        if PEDAL_ID in costs:
            data['pedalCost'] = costs[PEDAL_ID]
        return from_dict(data, 'floor')
    except ValueError:
        return None


INFO_COMMAND = 0x53


def _text(value):
    if isinstance(value, bytes):
        return value.decode('ascii', 'replace')
    if isinstance(value, dict):
        return {_text(k): _text(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_text(v) for v in value]
    return value


def ask_floor(firmware_tools, *, find=None, opener=None, timeout=1.5):
    """Ask the connected Floor which firmware it runs (editor command 0x53), before HX Edit takes the USB editor
    interface: one request on the editor service through the same WinUSB link the LT profile scan uses. The profile
    from its reply, or None (no Floor, stock firmware or a build without the command, no answer in time: right after
    a power-on the Floor does not answer USB for about a minute). Never raises: the caller keeps the configured profile."""
    try:
        import nam_inventory
        find = find or nam_inventory.device_path
        opener = opener or nam_inventory.open_link
        path = find()
        if not path:
            return None
        import sys
        sys.path.insert(0, str(firmware_tools))
        try:
            from hx_ctl import Channel, EDITOR, HxError
        finally:
            sys.path.pop(0)
        with opener(path) as link:
            channel = Channel(link, timeout=timeout, **EDITOR)
            try:
                reply = channel.request(INFO_COMMAND)
            except HxError:
                return None
            finally:
                channel.close()
        return from_identity(_text(reply))
    except Exception as error:                  # a probe: any failure means "not known", never a failed launch
        print(f'Could not ask the Floor which firmware it runs: {error}')
        return None


def record(resources, firmware):
    """Write the profile a prepared copy's catalog was built with into its res folder."""
    data = {k: v for k, v in asdict(firmware).items() if k != 'source'}
    Path(resources, RECORD).write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def recorded(resources):
    """The profile a prepared copy was built with, or the default for a copy prepared before records existed
    (a res/nam-pedal.flag from those builds still means the pedal block is in its catalog)."""
    path = Path(resources, RECORD)
    if path.is_file():
        data = json.loads(path.read_text(encoding='utf-8'))
        return from_dict({'namCost': data['nam_cost'], 'pedal': data['pedal'], 'pedalCost': data['pedal_cost']}, 'build')
    return FloorFirmware(pedal=Path(resources, 'nam-pedal.flag').is_file(), source='build')
