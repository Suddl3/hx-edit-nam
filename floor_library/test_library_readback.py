import unittest
import struct
from library_readback import ReadbackSession, match_export
from library_catalog import empty_catalog,add_wav,inspect_wav
from test_library_catalog import wav


class ReadbackTests(unittest.TestCase):
    def test_native_final_list_padding_quirk_preserves_exact_sample_identity(self):
        base=wav(); metadata=b'INFO'+b'x'*33
        chunks=base[12:]+b'LIST'+struct.pack('<I',len(metadata))+metadata
        raw=b'RIFF'+struct.pack('<I',len(chunks)+4)+b'WAVE'+chunks
        # General imports remain strict; only correlated native readback opts in.
        with self.assertRaises(ValueError):inspect_wav(raw)
        self.assertEqual(self.finish(raw=raw)['contentId'],self.key)
        self.assertEqual(self.session.slot_map(),{self.key:[9]})
        # A missing payload byte, wrong RIFF length, non-LIST final chunk,
        # or a missing interior padding byte cannot use this exemption.
        malformed=[raw[:-1],raw+b'xx',raw.replace(b'LIST',b'JUNK')]
        cut=bytearray(raw[:-1]);struct.pack_into('<I',cut,4,len(cut)-8);malformed.append(bytes(cut))
        interior=chunks+b'JUNK'+struct.pack('<I',2)+b'ab'
        malformed.append(b'RIFF'+struct.pack('<I',len(interior)+4)+b'WAVE'+interior)
        for damaged in malformed:
            with self.subTest(size=len(damaged)),self.assertRaises(ValueError):
                match_export(damaged,self.catalog)

    def setUp(self):
        self.catalog=empty_catalog()
        self.key=add_wav(self.catalog,wav(),'Friendly Amp',gear='amp')
        self.now=0
        self.session=ReadbackSession('fixture-device',self.catalog,clock=lambda:self.now)
        self.rows=[{'slot':s,'name':'same label','occupied':True} for s in (9,10,128)]
        self.session.set_inventory(self.rows,1)

    def finish(self,slot=9,raw=None,**kwargs):
        request=self.session.begin(slot)
        return self.session.complete(request,wav() if raw is None else raw,
            after=kwargs.get('after',next(r for r in self.rows if r['slot']==slot)),revision=kwargs.get('revision',1))

    def test_content_not_names_and_container_metadata(self):
        result=self.finish(raw=wav(metadata=b'export metadata'))
        self.assertEqual(result['contentId'],self.key)
        self.assertEqual(self.session.slot_map(),{self.key:[9]})
        self.assertEqual(self.finish(10,wav(gain=.1))['status'],'unmatched-nam')
        self.assertEqual(self.finish(10,wav(False))['status'],'ir')
        self.assertEqual(self.session.slot_map(),{self.key:[9]})

    def test_multiple_slots_and_reserved_preview(self):
        for slot in (9,10,128):self.finish(slot)
        self.assertEqual(self.session.slot_map(),{self.key:[9,10]})
        self.assertTrue(self.session.results[128]['previewSlot'])

    def test_serial_cancel_late_and_inventory_invalidation(self):
        request=self.session.begin(9)
        with self.assertRaises(ValueError):self.session.begin(10)
        self.session.cancel()
        self.assertEqual(self.session.complete(request,wav(),after=self.rows[0],revision=1)['reason'],'stale-request')
        self.finish()
        request=self.session.begin(10)
        self.session.set_inventory(self.rows,2)
        self.assertEqual(self.session.slot_map(),{})
        self.assertEqual(self.session.complete(request,wav(),after=self.rows[1],revision=2)['reason'],'stale-request')

    def test_post_export_races_and_malformed_data(self):
        for after in ({**self.rows[0],'name':'changed'},{**self.rows[0],'occupied':False},self.rows[1],None):
            self.setUp()
            self.assertEqual(self.finish(after=after)['status'],'discarded')
            self.assertEqual(self.session.slot_map(),{})
            with self.assertRaises(ValueError):self.session.begin(9)
        self.setUp()
        self.assertEqual(self.finish(revision=2)['reason'],'slot-changed')
        self.setUp()
        for raw in (b'bad',wav()[:-1],wav(gain=float('nan')),b'x'*(1024*1024+1)):
            self.assertEqual(self.finish(raw=raw)['status'],'unidentified')
        self.assertEqual(self.session.slot_map(),{})

    def test_timeout_and_export_failure_stop_without_retry(self):
        request=self.session.begin(9);self.now=6
        self.assertEqual(self.session.complete(request,wav(),after=self.rows[0],revision=1)['reason'],'timeout')
        self.session.set_inventory(self.rows,2)
        with self.assertRaises(ValueError):self.session.begin(9)
        other=ReadbackSession('fixture-device',self.catalog)
        other.set_inventory(self.rows,1);other.fail(other.begin(9))
        with self.assertRaises(ValueError):other.begin(9)

    def test_session_reset_unread_empty_and_invalid_inventory(self):
        for slot in (True,0,129,11):
            with self.assertRaises(ValueError):self.session.begin(slot)
        for rows in ([self.rows[0]]*2,[{'slot':9,'name':'x','occupied':1}]):
            with self.assertRaises(ValueError):self.session.set_inventory(rows,2)
        self.session.set_inventory([{'slot':9,'name':'','occupied':False}],2)
        with self.assertRaises(ValueError):self.session.begin(9)
        self.session.close()
        with self.assertRaises(ValueError):self.session.begin(9)
        self.assertEqual(self.session.slot_map(),{})

    def test_preview_only_catalog_is_not_kept_and_bad_bytes_rejected(self):
        self.catalog['captures'][self.key]['retention']='preview'
        self.assertEqual(match_export(wav(),self.catalog)['status'],'unmatched-nam')
        with self.assertRaises(ValueError):match_export('filename.wav',self.catalog)

    def test_global_revision_change_clears_all_mappings(self):
        self.finish(10)
        self.assertEqual(self.finish(revision=2)['status'],'discarded')
        self.assertEqual(self.session.slot_map(),{})
        with self.assertRaises(ValueError):self.session.begin(10)

    def test_other_connection_receipt_cannot_complete_request(self):
        other=ReadbackSession('fixture-device',self.catalog)
        other.set_inventory(self.rows,1)
        foreign=other.begin(9)
        own=self.session.begin(9)
        self.assertEqual(self.session.complete(foreign,wav(),after=self.rows[0],revision=1)['reason'],'stale-request')
        self.assertEqual(self.session.pending,own)
        self.assertEqual(self.session.complete(own,wav(),after=self.rows[0],revision=1)['status'],'matched')


if __name__=='__main__':unittest.main()
