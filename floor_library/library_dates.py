"""Recover date evidence from retained catalogs, without changing the active one."""
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re


def recover(snapshots, explicit_keeps=()):
    result = {}
    for path in snapshots:
        path = Path(path)
        match = re.search(r'(?<!\d)(20\d{6})(?!\d)', str(path))
        if not match:
            continue
        stamp = match[1]
        day = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:])).isoformat()
        raw = path.read_bytes()
        catalog = json.loads(raw)
        if catalog.get('schema') != 'hx-library-catalog-v1':
            continue
        for key, record in catalog['captures'].items():
            if record.get('kind') != 'nam' or record.get('retention') == 'preview':
                continue
            if key not in result or day < result[key]['day']:
                result[key] = {'day': day, 'kind': 'seen-by', 'precision': 'day',
                    'source': str(path), 'sourceSha256': hashlib.sha256(raw).hexdigest()}
    for key, day, report in explicit_keeps:
        date.fromisoformat(day)
        raw = Path(report).read_bytes()
        if key not in raw.decode('utf-8'):
            raise ValueError('Keep report does not identify the capture')
        result[key] = {'day': day, 'kind': 'added', 'precision': 'day',
            'source': str(report), 'sourceSha256': hashlib.sha256(raw).hexdigest()}
    return {'schema': 'hx-library-date-evidence-v1', 'captures': result}


def merge_dates(catalog, evidence_path):
    """Read-only presentation overlay. Fresh timestamps always take precedence."""
    catalog = deepcopy(catalog)
    evidence_path = Path(evidence_path)
    evidence = json.loads(evidence_path.read_text(encoding='utf-8')) if evidence_path.is_file() else {
        'schema':'hx-library-date-evidence-v1','captures':{}}
    if evidence.get('schema') != 'hx-library-date-evidence-v1':
        raise ValueError('Unsupported date evidence')
    for key, item in evidence.get('captures', {}).items():
        if key not in catalog['captures'] or catalog['captures'][key].get('addedAt'):
            continue
        date.fromisoformat(item['day'])
        if item.get('kind') not in ('added', 'seen-by'):
            continue
        source = Path(item['source'])
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != item['sourceSha256']:
            continue
        catalog['captures'][key]['dateEvidence'] = {k:item[k] for k in ('day','kind','precision')}
    catalog=merge_historical_file_dates(catalog,evidence_path.parent/'recovered-file-dates.json')
    return merge_file_dates(catalog, evidence_path.parent)


def merge_historical_file_dates(catalog, evidence_path):
    """Earlier content-matched files may refine a seen-by date, never addedAt."""
    from library_catalog import inspect_wav
    catalog=deepcopy(catalog)
    evidence_path=Path(evidence_path)
    if not evidence_path.is_file():return catalog
    evidence=json.loads(evidence_path.read_text(encoding='utf8'))
    if evidence.get('schema')!='hx-library-date-evidence-v1':raise ValueError('Unsupported file date evidence')
    for key,item in evidence.get('captures',{}).items():
        record=catalog['captures'].get(key)
        if (not record or record.get('kind')!='nam' or record.get('retention')=='preview'
                or record.get('addedAt')):continue
        previous=record.get('dateEvidence') or {}
        if previous.get('kind')=='added':continue
        try:
            day=date.fromisoformat(item['day'])
            if not date(2000,1,1)<=day<=date.today():continue
            if previous.get('day') and item['day']>=previous['day']:continue
            if item.get('kind') not in ('file-modified','file-created'):continue
            source=Path(item['source'])
            if not source.is_file() or source.stat().st_size>1024*1024:continue
            raw=source.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=item['sourceSha256']:continue
            if inspect_wav(raw)['contentId']!=key:continue
            record['dateEvidence']={'day':item['day'],'kind':item['kind'],'precision':'day','estimated':True}
        except (OSError,KeyError,ValueError,TypeError):continue
    return catalog


def merge_file_dates(catalog, library_root, *, now=None):
    """Fallback only: verified local file timestamps are estimates, never downloads.

    Explicit added dates and retained historical evidence take precedence. Do not
    use POSIX ctime (metadata changes) as creation time or trust copied timestamps
    as proof of installation. Nothing is persisted to the source catalog.
    """
    from library_catalog import inspect_wav
    catalog=deepcopy(catalog)
    root=Path(library_root).resolve()
    latest=now or datetime.now(timezone.utc)
    for key, record in catalog['captures'].items():
        if (record.get('kind')!='nam' or record.get('retention')=='preview'
                or record.get('addedAt') or record.get('dateEvidence') or not record.get('keptFile')):
            continue
        try:
            path=(root/record['keptFile']).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size>1024*1024:
                continue
            if inspect_wav(path.read_bytes())['contentId']!=key:
                continue
            stat=path.stat()
            candidates=[]
            for kind,stamp in (('file-modified',stat.st_mtime),('file-created',getattr(stat,'st_birthtime',None))):
                if stamp is None:continue
                stamp=datetime.fromtimestamp(stamp,timezone.utc)
                if datetime(2000,1,1,tzinfo=timezone.utc)<=stamp<=latest:
                    candidates.append((stamp,kind))
            if candidates:
                stamp,kind=min(candidates)
                record['dateEvidence']={'day':stamp.astimezone().date().isoformat(),
                    'kind':kind,'precision':'day','estimated':True}
        except (OSError,ValueError,OverflowError,TypeError):
            continue
    return catalog
