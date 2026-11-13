"""Native-export receipts and session identities; no USB, app, or user files."""
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from floor_inventory import FLOOR, FloorInventoryService, import_legacy_catalog, inspect_payload
from nam_library import Library, render_library


def wav(*, nam=True, tail=b'', samples=None):
    if samples is None:
        samples = bytearray(8192)
        if nam:
            struct.pack_into('<5I', samples, 0, 0x3f000000, 0xbf000000, 0x40400000, 0x3f800000, 0x3fe9e000)
    fmt = struct.pack('<HHIIHH', 3, 1, 48000, 192000, 4, 32)
    chunks = b'fmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(samples)) + samples + tail
    return b'RIFF' + struct.pack('<I', len(chunks) + 4) + b'WAVE' + chunks


class PayloadTests(unittest.TestCase):
    def test_identity_ignores_metadata_but_not_samples(self):
        raw = wav()
        found = inspect_payload(raw)
        self.assertEqual(found['kind'], 'nam')
        self.assertEqual(found, inspect_payload(wav(tail=b'LIST\x04\x00\x00\x00test')))
        changed = bytearray(raw)
        struct.pack_into('<f', changed, 44 + 100, .25)
        self.assertNotEqual(found['contentId'], inspect_payload(bytes(changed))['contentId'])
        self.assertEqual(inspect_payload(wav(nam=False))['kind'], 'ir')

    def test_padding_exception_is_only_for_final_native_list(self):
        raw = wav(tail=b'LIST\x03\x00\x00\x00abc')
        with self.assertRaises(ValueError): inspect_payload(raw)
        self.assertEqual(inspect_payload(raw, native_export=True)['kind'], 'nam')
        with self.assertRaises(ValueError): inspect_payload(wav(tail=b'JUNK\x03\x00\x00\x00abc'), native_export=True)

    def test_unsupported_nam_nonfinite_and_truncation_do_not_become_ir(self):
        raw = bytearray(wav())
        struct.pack_into('<f', raw, 44 + 8, 99)
        with self.assertRaises(ValueError): inspect_payload(bytes(raw))
        raw = bytearray(wav())
        struct.pack_into('<f', raw, 44 + 100, float('nan'))
        with self.assertRaises(ValueError): inspect_payload(bytes(raw))
        for broken in (wav()[:-1], wav() + b'X', b'RIFF'):
            with self.assertRaises(ValueError): inspect_payload(broken)

    def test_pedal_signature_supported_without_claiming_amp_type(self):
        raw = bytearray(wav())
        struct.pack_into('<I', raw, 44 + 16, 0x3f1cc000)
        self.assertEqual(inspect_payload(bytes(raw))['kind'], 'nam')


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 0
        self.sequence = 0
        self.service = FloorInventoryService(self.temp.name, clock=lambda: self.now)
        self.request = dict(token='0:1', slot=127, device=FLOOR, revision=3)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def submitted(self, slot=127):
        self.sequence += 1
        self.request['slot'] = slot
        prepared = self.service.prepare(self.request)
        event = dict(type='irExportObservation', request=f'session:{self.sequence}', sequence=self.sequence, slot=slot,
                     device=FLOOR, revision=3, path=prepared['path'], stage='submitted')
        self.assertIsNone(self.service.receive(event))
        return event

    def complete(self, event, raw=None, **overrides):
        Path(event['path']).write_bytes(wav() if raw is None else raw)
        return self.service.receive({**event, 'stage': 'completed', 'result': 0, 'details': [0], **overrides})

    def test_delayed_ui_completion_and_content_evidence(self):
        event = self.submitted()
        Path(event['path']).write_bytes(wav())
        waiting = {**event, 'stage': 'awaiting-unlock', 'result': 0, 'details': [0]}
        self.assertIsNone(self.service.receive(waiting))
        result = self.service.receive({**waiting, 'stage': 'completed'})
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['evidence']['contentId'], inspect_payload(wav())['contentId'])
        self.assertEqual(result['evidence']['automaticToken'], '0:1')
        self.assertNotIn('path', result['evidence'])

    def test_reconnect_rejects_late_completion(self):
        event = self.submitted()
        self.service.connection_changed()
        self.assertIsNone(self.complete(event))

    def test_fast_native_writer_and_reserved_slot_guard(self):
        with self.assertRaises(ValueError): self.service.prepare({**self.request, 'slot': 128})
        prepared = self.service.prepare(self.request)
        Path(prepared['path']).write_bytes(wav())
        result = self.service.receive(dict(stage='submitted', request='x', sequence=1, path=prepared['path'],
                                           slot=127, device=FLOOR, revision=3))
        self.assertIsNone(result)
        result = self.service.receive(dict(stage='completed', request='x', sequence=1, path=prepared['path'],
                                            slot=127, device=FLOOR, revision=3, result=0, details=[0]))
        self.assertEqual(result['status'], 'matched')

    def test_timeout_cancellation_releases_prearmed_target(self):
        old = self.submitted()
        self.service.cancel('discovery-timeout')
        new = self.submitted()
        self.assertNotEqual(old['path'], new['path'])
        self.assertIsNone(self.complete(old))
        self.assertEqual(self.complete(new)['status'], 'matched')

    def test_native_failure_wrong_revision_timeout_and_missing_file(self):
        for override in ({'result': 1}, {'revision': 4}, {'details': [False]}):
            with self.subTest(override=override):
                event = self.submitted()
                self.assertEqual(self.complete(event, **override)['status'], 'discarded')
        event = self.submitted()
        self.now = 31
        self.assertEqual(self.complete(event)['status'], 'discarded')

    def test_ir_and_unidentified_continue_without_evidence(self):
        for raw, state in ((wav(nam=False), 'ir'), (b'not a WAV', 'unidentified')):
            event = self.submitted()
            result = self.complete(event, raw)
            self.assertEqual(result['status'], state)
            self.assertNotIn('evidence', result)

    def test_serial_slots_get_private_distinct_files(self):
        first = self.submitted(1)
        with self.assertRaises(ValueError): self.service.prepare(self.request)
        self.complete(first)
        second = self.submitted(2)
        self.assertNotEqual(first['path'], second['path'])
        self.assertEqual(self.complete(second)['evidence']['slot'], 2)


class LibraryInventoryTests(unittest.TestCase):
    def test_legacy_copy_preserves_usable_wav_without_rewriting_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder, 'old'); root.mkdir()
            content = inspect_payload(wav())['contentId']
            (root / 'kept.wav').write_bytes(wav())
            old = {'captures': {content: dict(contentId=content, kind='nam', retention='kept',
                keptFile='kept.wav', names=['Old transport'], displayName='Nice Amp',
                override='amp', gearEvidence=[{'gear': 'pedal'}])}}
            catalog = root / 'catalog.json'; catalog.write_text(json.dumps(old))
            (root / 'recovered-dates.json').write_text(json.dumps({'schema':'hx-library-date-evidence-v1',
                'captures':{content:{'day':'2026-11-05','kind':'file-created'}}}))
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            library = Library(Path(folder, 'new'))
            plan = import_legacy_catalog(library, catalog)
            self.assertEqual(plan['ready'], 1); self.assertFalse(library.folder.exists())
            self.assertEqual(import_legacy_catalog(library, catalog, apply=True)['copied'], 1)
            entry = library.entries()[content]
            self.assertEqual((entry['name'], entry['gear'], entry['added'], entry['canTrain']),
                             ('Nice Amp', 'amp', '2026-11-05', False))
            exported = library.export(content, Path(folder, 'export'))
            self.assertEqual(exported.suffix, '.wav'); self.assertEqual(exported.read_bytes(), wav())
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
            with self.assertRaises(ValueError): import_legacy_catalog(library, catalog, apply=True)
            self.assertEqual(library.entries()[content]['irSlots'], [])

    def test_legacy_changed_content_is_reported_without_partial_import(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder, 'old'); root.mkdir()
            content = inspect_payload(wav())['contentId']
            (root / 'kept.wav').write_bytes(wav(nam=False))
            catalog = root / 'catalog.json'
            catalog.write_text(json.dumps({'captures': {content: dict(kind='nam', retention='kept', keptFile='kept.wav')}}))
            library = Library(Path(folder, 'new'))
            self.assertEqual(len(import_legacy_catalog(library, catalog)['skipped']), 1)
            with self.assertRaises(ValueError): import_legacy_catalog(library, catalog, apply=True)
            self.assertFalse(library.folder.exists())

    def test_verified_slots_require_payload_proof_and_do_not_persist(self):
        with tempfile.TemporaryDirectory() as folder:
            capture = Path(folder, 'Nice Amp.nam')
            capture.write_text('{"metadata":{"gear_type":"amp"}}')
            library = Library(Path(folder, 'library'))
            identity = library.add(capture)
            converted = Path(folder, 'unrelated-name.wav')
            converted.write_bytes(wav())
            content = library.register_payload(identity, converted)
            self.assertEqual(library.entries()[identity]['irSlots'], [])
            self.assertTrue(library.mark_pending(identity, 7))
            self.assertFalse(library.mark_pending(identity, 128))
            self.assertEqual(library.entries()[identity]['pendingSlots'], [7])
            self.assertTrue(library.set_slot_inventory({'epoch': 1, 'state': 'verified', 'entries': [[7, content], [127, content]]}))
            self.assertEqual(library.entries()[identity]['irSlots'], [7, 127])
            self.assertEqual(library.entries()[identity]['pendingSlots'], [])
            self.assertIn('IR slot', render_library(library.entries()))
            library.update(identity, name='Better')
            self.assertNotIn('irSlots', json.loads(library.catalog.read_text())[identity])
            fresh = Library(library.folder)
            self.assertEqual(fresh.entries()[identity]['irSlots'], [])
            self.assertTrue(library.set_slot_inventory({'epoch': 2, 'state': 'unverified', 'entries': []}))
            self.assertEqual(library.entries()[identity]['irSlots'], [])
            self.assertFalse(library.set_slot_inventory({'epoch': 1, 'state': 'verified', 'entries': [[7, content]]}))

    def test_bad_evidence_and_changed_original_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            capture = Path(folder, 'Amp.nam'); capture.write_text('{}')
            library = Library(Path(folder, 'library')); identity = library.add(capture)
            converted = Path(folder, 'Amp.wav'); converted.write_bytes(wav())
            (library.folder / (identity + '.nam')).write_text('changed')
            with self.assertRaises(ValueError): library.register_payload(identity, converted)
            for entries in ([[128, 'a' * 64]], [[1, 'bad']], [[1, 'a' * 64], [1, 'b' * 64]]):
                self.assertFalse(library.set_slot_inventory({'epoch': 1, 'state': 'verified', 'entries': entries}))


if __name__ == '__main__':
    unittest.main()
