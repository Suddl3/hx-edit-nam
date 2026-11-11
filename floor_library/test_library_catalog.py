import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest

import library_catalog as lib


def wav(nam=True, metadata=b'', gain=0.0):
    samples = [0.0] * 2048
    if nam:
        samples[:5] = [0.5, -0.5, 3.0, 2.0, struct.unpack('<f', struct.pack('<I', 0x3FE9E000))[0]]
    samples[100] = gain
    data = struct.pack('<2048f', *samples)
    fmt = struct.pack('<HHIIHH', 3, 1, 48000, 192000, 4, 32)
    chunks = b'fmt ' + struct.pack('<I', 16) + fmt
    if metadata:
        chunks += b'JUNK' + struct.pack('<I', len(metadata)) + metadata + b'\0' * (len(metadata) & 1)
    chunks += b'data' + struct.pack('<I', len(data)) + data
    return b'RIFF' + struct.pack('<I', len(chunks) + 4) + b'WAVE' + chunks


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = lib.empty_catalog()

    def add(self, **kwargs):
        return lib.add_wav(self.catalog, wav(), 'Plexi', **kwargs)

    def test_names_do_not_determine_payload_type(self):
        key = lib.add_wav(self.catalog, wav(False), 'NAM D-Lux', gear='amp')
        self.assertEqual(lib.category(self.catalog['captures'][key]), 'irs')
        self.assertEqual(self.catalog['captures'][self.add()]['kind'], 'nam')

    def test_container_metadata_does_not_break_identity(self):
        a = lib.inspect_wav(wav())
        b = lib.inspect_wav(wav(metadata=b'abc'))
        self.assertEqual(a, b)
        self.assertNotEqual(a, lib.inspect_wav(wav(gain=0.1)))

    def test_bad_and_partial_samples_never_become_irs(self):
        broken = bytearray(wav())
        struct.pack_into('<f', broken, 44 + 2 * 4, 8)
        nonfinite = wav(gain=float('nan'))
        for raw in (b'NAM profile', wav()[:-1], bytes(broken), nonfinite, wav() + b'garbage'):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                lib.inspect_wav(raw)

    def test_semantics_have_explicit_unknown_and_conflict(self):
        key = self.add()
        self.assertEqual(lib.category(self.catalog['captures'][key]), 'unidentified')
        self.add(gear='amp-cab')
        self.assertEqual(lib.category(self.catalog['captures'][key]), 'amp+cab')
        self.add(gear='amp', provenance='nam-metadata')
        self.assertEqual(lib.category(self.catalog['captures'][key]), 'unidentified')
        self.add(gear='pedal', override=True)
        self.assertEqual(lib.category(self.catalog['captures'][key]), 'pedals')

    def test_no_metadata_means_no_amp_assumption(self):
        key = self.add(gear='new-unknown-category')
        self.assertEqual(lib.category(self.catalog['captures'][key]), 'unidentified')

    def test_shared_storage_empty_unknown_and_verified_counts(self):
        key = self.add(gear='amp')
        result = lib.reconcile(self.catalog, [{'slot': 1, 'occupied': False},
            {'slot': 9, 'occupied': True, 'name': 'Plexi', 'contentId': key},
            {'slot': 10, 'occupied': True, 'name': 'Plexi'}])
        self.assertEqual(result['counts'], {'nam': 1, 'ir': 0, 'empty': 1, 'unidentified': 1, 'unread': 125})
        self.assertFalse(result['complete'])
        self.assertTrue(result['slots'][8]['selectable'])
        self.assertFalse(result['slots'][9]['selectable'])

    def test_cached_slot_and_same_name_replacement_cannot_select_wrong_amp(self):
        key = self.add(gear='amp')
        first = lib.reconcile(self.catalog, [{'slot': 13, 'occupied': True, 'name': 'Plexi', 'contentId': key}])
        self.assertTrue(first['slots'][12]['selectable'])
        second = lib.reconcile(self.catalog, [{'slot': 13, 'occupied': True, 'name': 'Plexi'}])
        self.assertFalse(second['slots'][12]['selectable'])
        self.assertFalse(lib.reconcile(self.catalog, [])['slots'][12]['selectable'])

    def test_rename_or_move_retains_semantics_with_current_payload(self):
        key = self.add(gear='pedal')
        result = lib.reconcile(self.catalog, [{'slot': 80, 'occupied': True, 'name': 'New Name', 'contentId': key}])
        self.assertEqual(result['slots'][79]['category'], 'pedals')
        self.assertEqual(result['slots'][79]['name'], 'New Name')

    def test_slots_validate_and_full_inventory_totals_128(self):
        result = lib.reconcile(self.catalog, [{'slot': s, 'occupied': False} for s in range(1, 129)])
        self.assertEqual(result['counts']['empty'], 128)
        self.assertTrue(result['complete'])
        for rows in ([{'slot': 0, 'occupied': False}], [{'slot': True, 'occupied': False}],
                     [{'slot': 1, 'occupied': False}] * 2, [{'slot': 1, 'occupied': 1}]):
            with self.assertRaises(ValueError): lib.reconcile(self.catalog, rows)

    def test_catalog_roundtrip_and_duplicate_import(self):
        key = self.add(gear='amp')
        self.add(gear='amp')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'catalog.json'
            lib.save(self.catalog, path)
            self.assertEqual(lib.load(path), self.catalog)
            self.assertFalse(path.with_suffix('.json.tmp').exists())
        self.assertEqual(len(self.catalog['captures']), 1)
        self.assertEqual(len(self.catalog['captures'][key]['gearEvidence']), 1)

    def test_new_session_reloads_capture_identity_and_metadata_without_slots(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            path, capture = folder / 'catalog.json', folder / 'capture.wav'
            capture.write_bytes(wav())
            first = lib.CatalogStore(path).record(capture, gear='pedal', name='SD1')
            second = lib.CatalogStore(path).record(capture, name='Renamed SD1')
            self.assertEqual(first, second)
            catalog = lib.load(path)
            self.assertEqual(catalog['captures'][first]['names'], ['SD1', 'Renamed SD1'])
            self.assertEqual(lib.category(catalog['captures'][first]), 'pedals')
            self.assertEqual(lib.reconcile(catalog, [])['counts']['unread'], 128)

    def test_sidecar_must_match_wav_not_merely_its_name(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'capture.source.json'
            (Path(folder) / 'capture.wav').write_bytes(wav())
            record = {'schema': 'floor-tone-source-v1', 'wavSha256': hashlib.sha256(wav()).hexdigest(),
                      'tone': {'gear': 'amp-cab'}, 'model': {'name': 'Example'}}
            path.write_text(json.dumps(record))
            key = lib.import_sidecar(self.catalog, path)
            self.assertEqual(lib.category(self.catalog['captures'][key]), 'amp+cab')
            (Path(folder) / 'capture.wav').write_bytes(wav(False))
            with self.assertRaises(ValueError): lib.import_sidecar(self.catalog, path)

    def test_legacy_slots_are_not_imported_as_current_device_facts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'library.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE captures(id,name,wav,file_gear,override,slot)')
                db.execute('CREATE TABLE sources(id,tone_gear)')
                db.execute('INSERT INTO captures VALUES(?,?,?,?,?,?)', ('a', 'SD1', wav(), None, None, 11))
                db.execute('INSERT INTO sources VALUES(?,?)', ('a', 'pedal'))
            db.close()
            self.assertEqual(lib.import_legacy_library(self.catalog, path), 1)
        self.assertEqual(lib.reconcile(self.catalog, [])['counts']['unread'], 128)
        self.assertEqual(lib.category(next(iter(self.catalog['captures'].values()))), 'pedals')


if __name__ == '__main__': unittest.main()
