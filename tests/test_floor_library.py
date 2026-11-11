"""Portable library suite: synthetic files and fake native boundaries only."""
from pathlib import Path
import unittest

def load_tests(loader, tests, pattern):
    directory = str(Path(__file__).resolve().parents[1] / 'floor_library')
    return unittest.TestLoader().discover(directory, pattern='test_*.py', top_level_dir=directory)
