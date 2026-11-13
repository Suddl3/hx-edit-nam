"""Replay the real synthetic file dialog and batch gate against disposable WAVs."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from nam_convert import stage_tone_import, validate_import
from test_floor_inventory import wav

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node is required for native import replay')
class ToneImportPipelineTests(unittest.TestCase):
    def test_staged_audition_reopen_save_and_rejected_native_batches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            capture = root / 'Amp Ω gain.wav'
            capture.write_bytes(wav())
            # Real staging creates fresh disposable files for repeated/reopened
            # auditions. Save preserves the original filename and source bytes.
            first = str(stage_tone_import(capture, root, audition=True))
            second = str(stage_tone_import(capture, root, audition=True))
            saved = str(stage_tone_import(capture, root))
            self.assertNotEqual(first, second)
            for staged in (first, second, saved):
                self.assertEqual(Path(staged).read_bytes(), capture.read_bytes())
            cabinet = root / 'cabinet.wav'
            cabinet.write_bytes(wav(nam=False))
            utf16_length = len(saved.encode('utf-16-le')) // 2
            scenarios = [
                {'file': first, 'accept': True},
                {'file': second, 'accept': True},
                {'file': saved, 'slot': 127, 'accept': True},
                {'file': saved, 'capacity': utf16_length + 2, 'slot': 127, 'accept': True},
                {'file': first, 'tamper': [second], 'error': 'different file'},
                {'file': first, 'unsupplied': True, 'error': 'different file'},
                {'file': first, 'slot': 126, 'tamper': [first, second], 'error': 'different file'},
                {'file': first, 'occupied': True, 'error': 'already occupied', 'validation': True},
                {'file': str(cabinet), 'error': 'cabinet IR', 'validation': True},
                {'file': saved, 'capacity': utf16_length + 1, 'error': 'does not fit'},
                {'file': first, 'nullBuffer': True, 'error': 'does not fit'},
                {'file': first + '\0other.wav', 'error': 'does not fit'},
            ]
            run = subprocess.run(['node', str(ROOT / 'tests' / 'tone_import_pipeline_probe.cjs')],
                                 input=json.dumps({'root': str(ROOT), 'source': str(ROOT / 'nam_import.js'),
                                                   'python': sys.executable, 'scenarios': scenarios}),
                                 capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual(json.loads(run.stdout), {'scenarios': len(scenarios), 'legacyRejected': True})
            # A disposable import can be consumed; its source remains usable for
            # another audition and the payload validator still checks the bytes.
            Path(first).unlink()
            reopened = Path(stage_tone_import(capture, root, audition=True))
            self.assertEqual(reopened.read_bytes(), capture.read_bytes())
            self.assertEqual(validate_import([reopened], 128, [], True), {128: reopened.stem})


if __name__ == '__main__':
    unittest.main()
