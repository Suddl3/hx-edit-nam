"""Files handed to HX Edit's import get names it can open; no device or converter required."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from nam_convert import convert_batch, import_name


class ImportNameTests(unittest.TestCase):
    def test_brackets_become_round(self):
        self.assertEqual(import_name(Path('Vox AC30 Top Boost 421 [10 layers].nam')), 'Vox AC30 Top Boost 421 (10 layers).wav')
        self.assertEqual(import_name(Path('Чистый {тон}.HIR')), 'Чистый (тон).HIR')

    def test_copied_wavs_use_the_safe_name(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, 'Cab [V30].wav')
            source.write_bytes(b'RIFF')
            converted = Path(convert_batch([source], folder, Path(__file__))[0])
            self.assertEqual((converted.name, converted.read_bytes()), ('Cab (V30).wav', b'RIFF'))

    def test_a_converter_that_never_finishes_is_one_unsupported_capture(self):
        # ValueError is what every caller reports per capture. TimeoutExpired is not one, and used to
        # end the browser session or stop the launch while the library was being indexed.
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, 'Slow [amp].nam')
            source.write_text('{}', encoding='utf-8')
            timeout = subprocess.TimeoutExpired(cmd='nam2ir', timeout=30)
            with patch('nam_convert.subprocess.run', side_effect=timeout):
                with self.assertRaisesRegex(ValueError, r'Slow \[amp\]\.nam\s+The conversion did not finish'):
                    convert_batch([source], folder, Path(__file__))
            self.assertFalse(list(Path(folder).rglob('Slow (amp).nam')))   # no staged copy left behind


if __name__ == '__main__':
    unittest.main()
