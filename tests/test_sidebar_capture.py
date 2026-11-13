"""Execute the sidebar's actual input hook with mocked Win32 capture state."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_sidebar_drag_cancellation_and_capture_ownership():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root / 'tests' / 'sidebar_capture_probe.cjs')],
                            capture_output=True, text=True, timeout=15, cwd=root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'sidebar capture probe passed' in result.stdout
