"""Verify retained PC payloads independently of device installation evidence."""
from pathlib import Path
from library_catalog import inspect_wav


def file_state(record, identity, catalog_path):
    if not record.get('keptFile'):
        return {'fileStatus': 'missing', 'fileDetail': 'Capture file missing on this PC'}
    if catalog_path is None:
        return {'fileStatus': 'unchecked', 'fileDetail': 'Local capture file has not been checked'}
    root = Path(catalog_path).resolve().parent
    try:
        path = (root / record['keptFile']).resolve(strict=True)
        if not path.is_relative_to(root) or path.stat().st_size > 1024 * 1024:
            raise ValueError('Invalid kept file path or size')
        inspected = inspect_wav(path.read_bytes())
        if inspected != {'contentId': identity, 'kind': 'nam'}:
            raise ValueError('Payload differs from catalog')
    except FileNotFoundError:
        return {'fileStatus': 'missing', 'fileDetail': 'Capture file missing on this PC'}
    except (OSError, ValueError, TypeError):
        return {'fileStatus': 'invalid', 'fileDetail': 'Local capture file does not match this entry; recover the original'}
    return {'fileStatus': 'ready', 'fileDetail': 'Available on this PC · Capture content verified'}
