"""The catalog tables the editor script gets: each model's DSP load and its block @type (a model dragged
from the block picker becomes a one-block favorite, whose @type HX Edit does not derive itself)."""
from pathlib import Path
import tempfile
import unittest

import msgpack

import nam_catalog


def model(symbol, category, load=1.0, params=()):
    # HelixModelDefs.bin keys and strings end with a NUL.
    return {'symbolicID\0': symbol + '\0', 'category\0': category, 'load\0': load,
            'params\0': [{'symbolicID\0': name + '\0'} for name in params]}


class ModelTableTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.res = Path(self.folder.name)
        definitions = [model('HD2_AmpUSDeluxeNrm', 1, 30.0), model('HD2_PreampVintage', 13), model('HD2_Cab2x12', 2),
                       model('HD2_CabMicIr', 19), model('HD2_ImpulseResponse1024', 16, 7.0), model('HD2_Looper', 15),
                       model('HD2_DelayAdriatic', 9, params=('Time', '@trails')), model('HD2_DistTeemah', 3, params=('Drive',)),
                       model('HD2_Nothing', 0), 'not a model']
        (self.res / 'HelixModelDefs.bin').write_bytes(msgpack.packb(definitions))

    def test_block_types_follow_hx_edits_own_rule(self):
        types = nam_catalog.model_types(self.res)
        self.assertEqual(types, {'HD2_AmpUSDeluxeNrm': 1, 'HD2_PreampVintage': 1, 'HD2_Cab2x12': 2, 'HD2_CabMicIr': 2,
                                 'HD2_ImpulseResponse1024': 5, 'HD2_Looper': 6, 'HD2_DelayAdriatic': 7,
                                 'HD2_DistTeemah': 0})

    def test_loads_by_symbol_and_nothing_for_a_missing_file(self):
        loads = nam_catalog.model_loads(self.res)
        self.assertEqual((loads['HD2_AmpUSDeluxeNrm'], loads['HD2_ImpulseResponse1024']), (30.0, 7.0))
        self.assertEqual(nam_catalog.model_types(self.res / 'missing'), {})
        self.assertEqual(nam_catalog.model_loads(self.res / 'missing'), {})


if __name__ == '__main__':
    unittest.main()
