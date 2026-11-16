"""Every test runs without the user's own settings.json and HX_EDIT_NAM_* variables: those turn on the
firmware's extra blocks and the mode chooser, which would open Windows dialogs in the middle of a test."""
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

# The app's code lives in src/ (the repository root holds the README, docs and tests).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


@pytest.fixture(autouse=True)
def no_user_settings(monkeypatch, tmp_path):
    for name in ('HX_EDIT_NAM_LAYERS', 'HX_EDIT_NAM_PEDAL', 'HX_EDIT_NAM_FIRMWARE'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr('nam_modes.CACHE', tmp_path / 'Converted')     # not the user's converted cache
    monkeypatch.setattr('tone3000.LINKS', tmp_path / 'tone-links.json')  # nor their TONE3000 links
    with patch('nam_catalog.load_settings', return_value={}):
        yield
