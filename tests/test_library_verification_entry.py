"""Replay the actual native tab hook; progress must not masquerade as a click."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_verification_entry_is_owner_navigation_not_progress_or_restoration():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root / 'tests/library_verification_entry_probe.cjs')],
                            cwd=root, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'library entry probe passed' in result.stdout
