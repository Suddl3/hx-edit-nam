from copy import deepcopy
from datetime import datetime, timezone
import os
import hashlib
import json
from pathlib import Path
import tempfile
import sqlite3
import unittest

from library_catalog import empty_catalog,add_wav
from library_dates import merge_dates,merge_file_dates,merge_historical_file_dates
from test_library_catalog import wav


class FileDateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.path=self.root/'capture.wav';self.path.write_bytes(wav())
        self.catalog=empty_catalog()
        self.key=add_wav(self.catalog,wav(),'Amp',gear='amp')
        self.record=self.catalog['captures'][self.key]
        self.record['keptFile']='capture.wav'
        self.record.pop('addedAt',None)
        self.stamp=datetime(2020,6,2,12,tzinfo=timezone.utc)
        os.utime(self.path,(self.stamp.timestamp(),self.stamp.timestamp()))

    def test_verified_file_fallback_is_estimate_and_read_only(self):
        before=deepcopy(self.catalog)
        merged=merge_file_dates(self.catalog,self.root)
        evidence=merged['captures'][self.key]['dateEvidence']
        self.assertEqual(evidence['kind'],'file-modified')
        self.assertEqual(evidence['day'],self.stamp.astimezone().date().isoformat())
        self.assertTrue(evidence['estimated'])
        self.assertEqual(self.catalog,before)
        self.assertEqual(merge_dates(self.catalog,self.root/'missing.json'),merged)

    def test_stronger_dates_are_preserved(self):
        for key,value in [('addedAt','2021-01-02T00:00:00Z'),('dateEvidence',{'day':'2019-01-01','kind':'seen-by'})]:
            with self.subTest(key=key):
                record=deepcopy(self.catalog);record['captures'][self.key][key]=value
                self.assertEqual(merge_file_dates(record,self.root),record)

    def test_unverified_missing_preview_and_outside_files_skipped(self):
        for change in ({'keptFile':'missing.wav'},{'keptFile':'../capture.wav'},{'retention':'preview'}):
            record=deepcopy(self.catalog);record['captures'][self.key].update(change)
            self.assertEqual(merge_file_dates(record,self.root),record)
        self.path.write_bytes(wav(gain=.1))
        self.assertEqual(merge_file_dates(self.catalog,self.root),self.catalog)

    def test_future_timestamps_do_not_become_dates(self):
        # Both this file's creation and modification are after the supplied cutoff.
        self.assertEqual(merge_file_dates(self.catalog,self.root,
            now=datetime(2010,1,1,tzinfo=timezone.utc)),self.catalog)

    def test_historical_file_refines_seen_by_but_not_added(self):
        evidence={'schema':'hx-library-date-evidence-v1','captures':{self.key:{
            'day':'2020-06-02','kind':'file-modified','precision':'day',
            'source':str(self.path),'sourceSha256':hashlib.sha256(wav()).hexdigest()}}}
        report=self.root/'recovered-file-dates.json';report.write_text(json.dumps(evidence))
        self.record['dateEvidence']={'day':'2026-11-09','kind':'seen-by'}
        merged=merge_historical_file_dates(self.catalog,report)
        self.assertEqual(merged['captures'][self.key]['dateEvidence']['day'],'2020-06-02')
        self.assertEqual(self.record['dateEvidence']['day'],'2026-11-09')
        for field,value in [('addedAt','2026-11-09T12:00:00Z'),('dateEvidence',{'day':'2026-11-09','kind':'added'})]:
            copy=deepcopy(self.catalog);copy['captures'][self.key][field]=value
            self.assertEqual(merge_historical_file_dates(copy,report),copy)
        self.path.write_bytes(wav(gain=.1))
        self.assertEqual(merge_historical_file_dates(self.catalog,report),self.catalog)

    def test_bounded_historical_search_matches_contents(self):
        from recover_capture_file_dates import recover_files
        result=recover_files(self.catalog,[self.root])
        self.assertEqual(result['captures'][self.key]['day'],self.stamp.astimezone().date().isoformat())
        self.assertEqual(recover_files(self.catalog,[self.root],max_files=0)['audit']['complete'],False)
        self.path.write_bytes(wav(False))
        self.assertEqual(recover_files(self.catalog,[self.root])['captures'],{})

    def test_original_search_requires_exact_legacy_pair(self):
        from recover_capture_file_dates import recover_originals
        legacy=self.root/'legacy.sqlite3'
        with sqlite3.connect(legacy) as db:
            db.execute('CREATE TABLE captures(original BLOB,wav BLOB)')
            db.execute('INSERT INTO captures VALUES(?,?)',(b'original capture',wav()))
            db.execute('INSERT INTO captures VALUES(?,?)',(b'broken',b'bad wav'))
        db.close()
        original=self.root/'unrelated-name.nam';original.write_bytes(b'original capture')
        os.utime(original,(self.stamp.timestamp(),self.stamp.timestamp()))
        before=legacy.read_bytes()
        recovered=recover_originals(self.catalog,legacy,[self.root])
        self.assertEqual(recovered['captures'][self.key]['day'],self.stamp.astimezone().date().isoformat())
        self.assertEqual(legacy.read_bytes(),before)
        original.write_bytes(b'not the same capture')
        self.assertEqual(recover_originals(self.catalog,legacy,[self.root])['captures'],{})
        self.assertFalse(recover_originals(self.catalog,legacy,[self.root],max_files=0)['audit']['complete'])

    def test_historical_overlay_reaches_render_rows(self):
        from nam_library_view import library_rows
        report={'schema':'hx-library-date-evidence-v1','captures':{self.key:{
            'day':'2020-06-02','kind':'file-modified','precision':'day',
            'source':str(self.path),'sourceSha256':hashlib.sha256(wav()).hexdigest()}}}
        (self.root/'recovered-file-dates.json').write_text(json.dumps(report))
        self.record['dateEvidence']={'day':'2026-11-09','kind':'seen-by'}
        rows=library_rows(merge_dates(self.catalog,self.root/'missing.json'))
        self.assertEqual(rows[0]['dateEvidence']['day'],'2020-06-02')
        self.assertTrue(rows[0]['dateEvidence']['estimated'])


if __name__=='__main__':unittest.main()
