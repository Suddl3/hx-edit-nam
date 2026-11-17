import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import msgpack

import floor_firmware
from floor_firmware import FloorFirmware
from nam_catalog import ROOT, prepare_copy


BASELINE = ROOT / 'build' / 'HX Edit' / 'res'


class FloorFirmwareProfileTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop('HX_EDIT_FLOOR_FIRMWARE', None)

    def test_default_is_the_standard_image(self):
        firmware = floor_firmware.profile({})
        self.assertEqual((firmware.nam_cost, firmware.pedal, firmware.source), (3600, False, 'default'))
        self.assertEqual(firmware.nam_load, 36.0)

    def test_settings_then_environment(self):
        firmware = floor_firmware.profile({'floorFirmware': {'namCost': 3250}})
        self.assertEqual((firmware.nam_load, firmware.pedal, firmware.source), (32.5, False, 'settings'))
        os.environ['HX_EDIT_FLOOR_FIRMWARE'] = '{"pedal": true, "pedalCost": 900}'
        firmware = floor_firmware.profile({'floorFirmware': {'namCost': 3250}})
        self.assertEqual((firmware.nam_cost, firmware.pedal, firmware.pedal_load, firmware.source),
                         (3600, True, 9.0, 'environment'))
        self.assertEqual(firmware.describe(), 'NAM A2 Lite 36% + NAM Pedal 9%')

    def test_bad_values_are_refused(self):
        for data in ({'namCost': 32.5}, {'namCost': 900}, {'namCost': True}, {'pedal': 'yes'},
                     {'pedalCost': 50}, {'layers': True}, []):
            with self.assertRaises(ValueError):
                floor_firmware.profile({'floorFirmware': data})
        os.environ['HX_EDIT_FLOOR_FIRMWARE'] = 'not json'
        with self.assertRaisesRegex(ValueError, 'not valid JSON'):
            floor_firmware.profile({})

    def test_record_round_trip_and_legacy_pedal_flag(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(floor_firmware.recorded(temp), FloorFirmware(source='build'))
            Path(temp, 'nam-pedal.flag').write_text('x', encoding='ascii')
            self.assertTrue(floor_firmware.recorded(temp).pedal)          # copies built before the record
            firmware = FloorFirmware(nam_cost=3250, pedal=True, pedal_cost=800, source='settings')
            floor_firmware.record(temp, firmware)
            back = floor_firmware.recorded(temp)
            self.assertEqual((back.nam_cost, back.pedal, back.pedal_cost, back.source), (3250, True, 800, 'build'))


class FloorIdentityTests(unittest.TestCase):
    def test_reply_sets_costs_and_pedal(self):
        firmware = floor_firmware.from_identity({'fmt': 1, 'blocks': [[380, 3250]], 'feat': ['pair', 'mirror']})
        self.assertEqual((firmware.nam_cost, firmware.pedal, firmware.source), (3250, False, 'floor'))
        firmware = floor_firmware.from_identity({'fmt': 1, 'blocks': [[380, 3600], [768, 900]], 'feat': []})
        self.assertEqual((firmware.nam_load, firmware.pedal, firmware.pedal_cost), (36.0, True, 900))

    def test_unknown_replies_are_ignored(self):
        for reply in (None, [], {}, {'fmt': 2, 'blocks': [[380, 3600]]}, {'fmt': 1, 'blocks': [[768, 850]]},
                      {'fmt': 1, 'blocks': [[380, 99999]]}, {'fmt': 1, 'blocks': 'x'}):
            self.assertIsNone(floor_firmware.from_identity(reply))


FAKE_HX_CTL = """
class HxError(Exception):
    pass
EDITOR = dict(local=0x1000, remote=0x03ED, svc=6)
REPLY = None
CLOSED = []
class Channel:
    def __init__(self, link, timeout, **stream):
        assert stream == EDITOR and link == 'link'
    def request(self, cmd, params=None):
        assert cmd == 0x53
        if isinstance(REPLY, Exception):
            raise REPLY
        return REPLY
    def close(self):
        CLOSED.append(True)
"""


class AskFloorTests(unittest.TestCase):
    def setUp(self):
        self.tools = tempfile.TemporaryDirectory()
        self.addCleanup(self.tools.cleanup)
        Path(self.tools.name, 'hx_ctl.py').write_text(FAKE_HX_CTL, encoding='utf-8')
        import sys
        sys.modules.pop('hx_ctl', None)
        self.addCleanup(sys.modules.pop, 'hx_ctl', None)

    def ask(self, reply, path='floor'):
        from contextlib import contextmanager
        import sys

        @contextmanager
        def opener(p):
            self.assertEqual(p, 'floor')
            yield 'link'
        sys.path.insert(0, self.tools.name)
        try:
            import hx_ctl
        finally:
            sys.path.pop(0)
        hx_ctl.REPLY = reply if not callable(reply) else reply(hx_ctl)
        result = floor_firmware.ask_floor(self.tools.name, find=lambda: path, opener=opener)
        return result, hx_ctl.CLOSED

    def test_reply_with_raw_keys_gives_the_profile(self):
        firmware, closed = self.ask({b'fmt': 1, b'blocks': [[380, 3250]], b'feat': [b'pair', b'mirror']})
        self.assertEqual((firmware.nam_cost, firmware.pedal, firmware.source), (3250, False, 'floor'))
        self.assertEqual(closed, [True])

    def test_refusal_no_floor_or_failure_give_none(self):
        self.assertEqual(self.ask(lambda m: m.HxError('cmd 0x53 refused: status 255'))[0], None)
        self.assertEqual(self.ask({b'fmt': 1}, path=None)[0], None)
        self.assertEqual(self.ask(lambda m: OSError('timed out'))[0], None)
        self.assertEqual(self.ask({b'fmt': 9, b'blocks': [[380, 3600]]})[0], None)


@unittest.skipUnless(BASELINE.is_dir(), 'requires the prepared HX Edit 3.82 baseline')
class FloorCatalogTests(unittest.TestCase):
    def setUp(self):
        for name in ('nam_catalog.load_settings', 'nam_catalog.read_custom_names'):
            mocked = patch(name, return_value={})
            mocked.start()
            self.addCleanup(mocked.stop)

    def catalog(self, firmware):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        res = prepare_copy(temp.name, floor=firmware).parent / 'res'
        symbols = json.loads((res / 'Helix.sym').read_bytes())
        original = json.loads((BASELINE / 'Helix.sym').read_bytes())
        loads = {m['symbolicID\0'].rstrip('\0'): round(m['load\0'], 3)
                 for m in msgpack.unpackb((res / 'HelixModelDefs.bin').read_bytes())
                 if m['symbolicID\0'].startswith('HD2_AmpNAM')}
        changed = [i for i, s in enumerate(symbols) if s != original[i]]
        return changed, loads, floor_firmware.recorded(res)

    def test_standard_floor_matches_the_plain_catalog(self):
        changed, loads, recorded = self.catalog(FloorFirmware())
        self.assertEqual((changed, loads), ([380], {'HD2_AmpNAMLite': 36.0}))
        self.assertEqual((recorded.nam_cost, recorded.pedal), (3600, False))

    def test_pair_floor_declares_its_lower_cost(self):
        changed, loads, recorded = self.catalog(FloorFirmware(nam_cost=3250))
        self.assertEqual((changed, loads), ([380], {'HD2_AmpNAMLite': 32.5}))
        self.assertEqual(recorded.nam_cost, 3250)

    def test_pedal_floor_adds_only_the_pedal_block(self):
        changed, loads, recorded = self.catalog(FloorFirmware(nam_cost=3250, pedal=True))
        self.assertEqual(changed, [380, 768])                      # no LT layer blocks on the Floor
        self.assertEqual(loads, {'HD2_AmpNAMLite': 32.5, 'HD2_AmpNAMPedal': 8.5})
        self.assertTrue(recorded.pedal)


if __name__ == '__main__':
    unittest.main()
