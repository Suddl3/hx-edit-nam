"""Bounded offline search for dated local WAV copies matching library contents."""
import argparse
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
import sqlite3
import time
sys.path.insert(0,str(Path(__file__).resolve().parent))
from library_catalog import load,inspect_wav


def recover_files(catalog,roots,*,seconds=30,max_files=5000):
    end=time.monotonic()+seconds
    found={};examined=0;complete=True
    now=datetime.now(timezone.utc)
    for root in map(Path,roots):
        for path in root.rglob('*.wav'):
            if time.monotonic()>=end or examined>=max_files:
                complete=False;break
            examined+=1
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):continue
                stat=path.stat()
                if not 12<=stat.st_size<=1024*1024:continue
                raw=path.read_bytes();parsed=inspect_wav(raw);key=parsed['contentId']
                record=catalog['captures'].get(key)
                if parsed['kind']!='nam' or record is None or record.get('retention')=='preview':continue
                stamps=[]
                for kind,stamp in [('file-modified',stat.st_mtime),('file-created',getattr(stat,'st_birthtime',None))]:
                    if stamp is None:continue
                    value=datetime.fromtimestamp(stamp,timezone.utc)
                    if datetime(2000,1,1,tzinfo=timezone.utc)<=value<=now:stamps.append((value,kind))
                if not stamps:continue
                stamp,kind=min(stamps);day=stamp.astimezone().date().isoformat()
                if key not in found or day<found[key]['day']:
                    found[key]={'day':day,'kind':kind,'precision':'day','estimated':True,
                        'source':str(path.resolve()),'sourceSha256':hashlib.sha256(raw).hexdigest()}
            except (OSError,ValueError,OverflowError):continue
        if not complete:break
    return {'schema':'hx-library-date-evidence-v1','captures':found,
        'audit':{'filesExamined':examined,'complete':complete,'deviceAccess':False,
            'meaning':'Earliest matching local file timestamp, not a confirmed download or library-add date'}}


def recover_originals(catalog,legacy,roots,*,seconds=30,max_files=5000):
    """Match original bytes to a legacy NAM/WAV pair, never by file title."""
    pairs={}
    with closing(sqlite3.connect(Path(legacy).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        for original,wave in db.execute('SELECT original,wav FROM captures WHERE wav IS NOT NULL'):
            if not isinstance(original,bytes):continue
            try:
                parsed=inspect_wav(wave);key=parsed['contentId']
            except (ValueError,TypeError):continue
            record=catalog['captures'].get(key)
            if parsed['kind']=='nam' and record and record.get('retention')!='preview':
                pairs[hashlib.sha256(original).hexdigest()]=key
    found={};examined=0;complete=True;end=time.monotonic()+seconds
    now=datetime.now(timezone.utc)
    for root in map(Path,roots):
        for path in root.rglob('*.nam'):
            if time.monotonic()>=end or examined>=max_files:complete=False;break
            examined+=1
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):continue
                stat=path.stat()
                if not 1<=stat.st_size<=2*1024*1024:continue
                digest=hashlib.sha256(path.read_bytes()).hexdigest();key=pairs.get(digest)
                if key is None:continue
                stamps=[]
                for kind,stamp in [('file-modified',stat.st_mtime),('file-created',getattr(stat,'st_birthtime',None))]:
                    if stamp is None:continue
                    value=datetime.fromtimestamp(stamp,timezone.utc)
                    if datetime(2000,1,1,tzinfo=timezone.utc)<=value<=now:stamps.append((value,kind))
                if not stamps:continue
                stamp,kind=min(stamps);day=stamp.astimezone().date().isoformat()
                if key not in found or day<found[key]['day']:
                    found[key]={'day':day,'kind':kind,'precision':'day','estimated':True,
                        'source':str(path.resolve()),'sourceSha256':digest,
                        'identityEvidence':'Exact original bytes paired with matching canonical WAV in legacy catalog'}
            except (OSError,ValueError,OverflowError):continue
        if not complete:break
    return {'schema':'hx-library-date-evidence-v1','captures':found,
        'audit':{'filesExamined':examined,'complete':complete,'deviceAccess':False,
            'meaning':'Original NAM file timestamp; file copies can alter it'}}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog',type=Path,required=True)
    p.add_argument('--root',type=Path,action='append',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--legacy',type=Path)
    args=p.parse_args()
    result=(recover_originals(load(args.catalog),args.legacy,args.root) if args.legacy
        else recover_files(load(args.catalog),args.root))
    with args.output.open('x',encoding='utf8') as out:json.dump(result,out,indent=2)
    print(json.dumps({'matched':len(result['captures']),**result['audit'],
        'dates':{k:v['day'] for k,v in result['captures'].items()}},indent=2))
