"""Offline policy for the original Floor A2 Lite integration profile.

Selecting this profile does not detect or certify the installed firmware. It avoids
the separate startup WinUSB scan and refuses unsupported catalog settings.
"""

FLOOR_REFERENCE_OPTIONS = {'strictPreview': True, 'lastPermanentSlot': 127}


def require_plain_catalog(*, layers, pedal):
    if layers or pedal:
        raise ValueError('The Floor reference profile supports ordinary NAM A2 Lite only. '
                         'The current layers/pedal catalog settings are incompatible; '
                         'no editor or device session was started.')


def startup_profiles(*, floor_reference, scan, converter_directory, hx_nam=None):
    """Never invoke a custom USB scanner for the Floor reference launch."""
    if floor_reference:
        if hx_nam:
            raise ValueError('Choose either the Floor reference or an HX Stomp/Effects profile.')
        return {}, False
    try:
        return scan(converter_directory, hx_nam), False
    except Exception:
        if hx_nam:
            raise
        return {}, True
