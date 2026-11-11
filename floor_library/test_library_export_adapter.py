from pathlib import Path
import tempfile
import unittest

from library_catalog import empty_catalog,add_wav
from library_readback import ReadbackSession
from library_export_adapter import ExportReadbackAdapter
from test_library_catalog import wav


class ExportAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.catalog=empty_catalog();self.key=add_wav(self.catalog,wav(),'Amp')
        self.now=0
        self.session=ReadbackSession('device',self.catalog,clock=lambda:self.now)
        self.row={'slot':127,'occupied':True,'name':'Native name'}
        self.session.set_inventory([self.row],1)
        self.adapter=ExportReadbackAdapter(self.session,self.temp.name)

    def finish(self,plan,**kwargs):
        args=dict(reported_path=plan.destination,native_success=True,after=self.row,revision=1)
        args.update(kwargs)
        return self.adapter.complete(plan,**args)

    def test_exact_completed_file_maps_slot(self):
        plan=self.adapter.prepare(127);self.assertFalse(plan.destination.exists())
        plan.destination.write_bytes(wav(metadata=b'export'))
        self.assertEqual(self.finish(plan)['status'],'matched')
        self.assertEqual(self.session.slot_map(),{self.key:[127]})
        self.assertEqual(self.finish(plan)['reason'],'stale-completion')

    def test_no_completion_no_map_and_single_flight(self):
        plan=self.adapter.prepare(127);plan.destination.write_bytes(wav())
        self.assertEqual(self.session.slot_map(),{})
        with self.assertRaises(ValueError):self.adapter.prepare(127)
        self.assertEqual(self.finish(plan,native_success=False)['reason'],'native-export-failed')
        with self.assertRaises(ValueError):self.adapter.prepare(127)

    def test_wrong_path_and_incomplete_file_stop(self):
        for mutation in ('other','relative','missing','short','huge'):
            self.setUp();plan=self.adapter.prepare(127)
            path=plan.destination
            if mutation=='other':path=Path(self.temp.name)/'other.wav';path.write_bytes(wav())
            elif mutation=='relative':path=Path('capture.wav')
            elif mutation=='short':path.write_bytes(b'RIFF')
            elif mutation=='huge':path.write_bytes(b'x'*(1024*1024+1))
            self.assertEqual(self.finish(plan,reported_path=path)['status'],'discarded')
            self.assertEqual(self.session.slot_map(),{})
            with self.assertRaises(ValueError):self.adapter.prepare(127)

    def test_cancel_timeout_and_revision_change(self):
        plan=self.adapter.prepare(127);plan.destination.write_bytes(wav())
        self.adapter.cancel();self.assertEqual(self.finish(plan)['reason'],'stale-completion')
        plan=self.adapter.prepare(127);plan.destination.write_bytes(wav());self.now=6
        self.assertEqual(self.finish(plan)['reason'],'timeout')
        self.setUp();plan=self.adapter.prepare(127);plan.destination.write_bytes(wav())
        self.session.set_inventory([self.row],2)
        self.assertEqual(self.finish(plan)['reason'],'invalidated-request')

    def test_same_name_replacement_and_disconnect(self):
        plan=self.adapter.prepare(127);plan.destination.write_bytes(wav(gain=.1))
        self.assertEqual(self.finish(plan)['status'],'unmatched-nam')
        self.assertEqual(self.session.slot_map(),{})
        plan=self.adapter.prepare(127);plan.destination.write_bytes(wav())
        self.adapter.close();self.assertEqual(self.finish(plan)['reason'],'stale-completion')


if __name__=='__main__':unittest.main()
