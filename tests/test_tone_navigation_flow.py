"""Native navigation selection/cleanup replay; no app, network, or device."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node is required for navigation replay')
class ToneNavigationFlowTests(unittest.TestCase):
    def test_real_navigation_hooks_auto_bind_cleanup_geometry_and_lifetime(self):
        run = subprocess.run(['node', str(ROOT / 'tests' / 'tone_navigation_flow_probe.cjs'),
                              str(ROOT / 'tone_view.js')], capture_output=True, text=True,
                             encoding='utf-8', timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('29 navigation flow scenarios passed', run.stdout)


if __name__ == '__main__':
    unittest.main()
