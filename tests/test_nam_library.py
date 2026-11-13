"""Local NAM library: storage, de-duplication and the library page; no device required."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nam_library import CatalogError, Library, render_library


class LibraryTests(unittest.TestCase):
    def test_export_rejects_metadata_path_escape_and_changed_original(self):
        with tempfile.TemporaryDirectory() as folder:
            capture = Path(folder, 'Deluxe.nam')
            capture.write_text('{}', encoding='utf-8')
            library = Library(Path(folder, 'library'))
            identity = library.add(capture)
            entries = library._read()
            for name in ('../outside', '..\\outside', 'C:\\outside', 'nested/name', 'NAM:stream'):
                entries[identity]['irName'] = name
                library._write(entries)
                with self.assertRaises(ValueError): library.export(identity, Path(folder, 'export'))
            self.assertFalse(Path(folder, 'export').exists())
            entries[identity]['irName'] = 'Deluxe'
            library._write(entries)
            Path(library.folder, identity + '.nam').write_text('{"changed":true}', encoding='utf-8')
            with self.assertRaises(ValueError): library.export(identity, Path(folder, 'export'))
            self.assertFalse(Path(folder, 'export').exists())

    def test_an_unreadable_catalog_is_never_overwritten_with_a_new_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder, 'Deluxe.nam'), Path(folder, 'Plexi.nam')
            first.write_text('{"a":1}', encoding='utf-8')
            second.write_text('{"b":2}', encoding='utf-8')
            library = Library(Path(folder, 'library'))
            identity = library.add(first, name='Deluxe Reverb', gear='amp')
            for damaged in (b'', b'{"truncated": ', b'[]', b'\xff\xfe\x00not json'):
                with self.subTest(damaged=damaged):
                    library.catalog.write_bytes(damaged)
                    self.assertEqual(library.entries(), {})          # a reader can stay open
                    for change in (lambda: library.add(second), lambda: library.update(identity, name='x'),
                                   lambda: library.reset_gear(identity), lambda: library.remove(identity)):
                        with self.assertRaises(CatalogError):
                            change()
                    self.assertEqual(library.catalog.read_bytes(), damaged)   # left exactly as found
                    self.assertTrue(Path(library.folder, identity + '.nam').is_file())
            self.assertFalse(list(library.folder.glob('*.tmp')))

    def test_catalog_with_a_byte_order_mark_is_read_and_each_write_keeps_a_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            capture = Path(folder, 'Deluxe.nam')
            capture.write_text('{}', encoding='utf-8')
            library = Library(Path(folder, 'library'))
            identity = library.add(capture, name='Deluxe Reverb')
            self.assertFalse(library.backup.exists())                # nothing preceded the first write
            saved = library.catalog.read_text(encoding='utf-8')
            library.catalog.write_text(saved, encoding='utf-8-sig')  # as another tool may save it
            self.assertEqual(library.entries()[identity]['name'], 'Deluxe Reverb')
            library.update(identity, name='Deluxe')
            self.assertEqual(json.loads(library.backup.read_text(encoding='utf-8-sig'))[identity]['name'], 'Deluxe Reverb')
            self.assertEqual(library.entries()[identity]['name'], 'Deluxe')
            self.assertFalse(list(library.folder.glob('*.tmp')))

    def test_a_failed_catalog_write_does_not_delete_the_capture_file(self):
        with tempfile.TemporaryDirectory() as folder:
            capture = Path(folder, 'Deluxe.nam')
            capture.write_text('{}', encoding='utf-8')
            library = Library(Path(folder, 'library'))
            identity = library.add(capture)
            with patch.object(library, '_write', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    library.remove(identity)
            self.assertTrue(Path(library.folder, identity + '.nam').is_file())
            self.assertIn(identity, library.entries())

    def test_keeps_one_entry_per_capture_and_escapes_the_page(self):
        with tempfile.TemporaryDirectory() as folder:
            capture = Path(folder, 'Plexi Crunch 42.nam')
            capture.write_text(json.dumps({'metadata': {'gear_type': 'pedal_amp'}}), encoding='utf-8')
            library = Library(Path(folder, 'library'))
            first = library.add(capture, name='Plexi </script><b>', source='https://www.tone3000.com/tones/42')
            self.assertEqual(library.add(capture), first)
            entry = library.entries()[first]
            self.assertEqual((entry['gear'], entry['irName'], entry['name']), ('pedal', 'Plexi Crunch 42', 'Plexi </script><b>'))

            library.update(first, gear='amp')
            with self.assertRaises(ValueError):
                library.update(first, gear='synth')
            exported = library.export(first, Path(folder, 'export'))
            self.assertEqual((exported.name, exported.read_bytes()), ('Plexi Crunch 42.nam', capture.read_bytes()))

            page = render_library(library.entries())
            self.assertNotIn('</script><b>', page)
            self.assertIn('"gear": "amp"', page)

            library.remove(first)
            self.assertEqual(library.entries(), {})
            self.assertFalse(Path(folder, 'library', f'{first}.nam').exists())


if __name__ == '__main__':
    unittest.main()
