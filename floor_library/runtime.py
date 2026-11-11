"""Windows Floor library host. Uses the editor's transport, never a USB preflight."""
import hashlib
import json
import os
from pathlib import Path
import sys
from threading import Event, local


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def default_library_path():
    return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'HX Edit NAM' / 'NAM Library' / 'catalog.json'


def validate_nam_source(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    models = [s['model'] for s in data['config']['submodels']] if data.get('architecture') == 'SlimmableContainer' else [data]
    lite = [m for m in models if m.get('architecture') == 'WaveNet'
            and m.get('config', {}).get('layers', [{}])[0].get('channels') == 3]
    if not lite:
        raise ValueError('This firmware needs an A2 Lite three-channel capture; Full-only captures cannot be imported.')
    rate = lite[0].get('sample_rate') or data.get('sample_rate')
    if rate != 48000:
        raise ValueError('A verified 48 kHz capture is required for this Floor candidate.')
    # The established converter performs the remaining architecture/weight checks.


def configure_modules(package, workspace, catalog_path=None, on_catalog_change=None):
    # Keep sealed candidate modules immutable across ordinary launches.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(package / 'modules'))
    import nam_convert
    import tone3000
    original_convert = nam_convert.convert_batch
    original_get_tone = tone3000.get_tone
    original_prepare = tone3000.prepare_model
    from preview_policy import CaptureRetention
    retention = CaptureRetention(workspace, catalog_path or default_library_path())
    catalog = retention.kept
    context = local()

    def notify_catalog():
        if on_catalog_change:
            from library_catalog import load
            from nam_library_view import library_rows
            from library_dates import merge_dates
            on_catalog_change(library_rows(merge_dates(load(catalog.path), catalog.path.parent / 'recovered-dates.json'), catalog_path=catalog.path) if catalog.path.exists() else [])

    def convert(files, directory, converter):
        metadata = []
        for file in files:
            gear = None
            if Path(file).suffix.lower() == '.nam':
                validate_nam_source(file)
                model = json.loads(Path(file).read_text(encoding='utf-8'))
                if isinstance(model.get('metadata'), dict):
                    gear = model['metadata'].get('gear_type')
            metadata.append(gear)
        converted = original_convert(files, directory, converter)
        previewing = getattr(context, 'preview', False)
        for file, gear in zip(converted, metadata):
            if Path(file).suffix.lower() == '.wav':
                if nam_convert.is_nam_wav(Path(file)):
                    retention.preview.record(file, gear=gear, provenance='nam-metadata' if gear else 'local')
                    if not previewing:
                        retention.keep(file)
        if converted and not previewing:
            notify_catalog()
        return converted

    def get_tone(tone_id, token):
        tone, models = original_get_tone(tone_id, token)
        for model in models:
            model['_floor_source'] = {k: tone.get(k) for k in ('id', 'title', 'gear', 'url', 'license')}
        return tone, models

    def prepare_model(model, token, directory, converter):
        previous = getattr(context, 'preview', False)
        context.preview = True
        try:
            filename = original_prepare(model, token, directory, converter)
        finally:
            context.preview = previous
        source = model.get('_floor_source', {})
        # Preserve semantics from Tone3000, never infer amp/cab from the title.
        record = {'schema': 'floor-tone-source-v1', 'tone': source,
                  'model': {k: model.get(k) for k in ('id', 'name', 'architecture_version')},
                  'classification': source.get('gear') or 'unidentified',
                  'wavSha256': file_sha(filename), 'deviceSlot': None}
        Path(filename).with_suffix('.source.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
        retention.preview.record(filename, gear=source.get('gear'), provenance='tone3000', name=model.get('name'))
        return filename

    def keep_preview(filename):
        identity = retention.keep(filename)
        notify_catalog()
        return identity

    def classify_capture(identity, gear, revision):
        try:
            return catalog.classify(identity, gear, revision)
        finally:
            notify_catalog()  # Also refresh a rejected stale classification.

    nam_convert.convert_batch = tone3000.convert_batch = convert
    tone3000.get_tone, tone3000.prepare_model = get_tone, prepare_model
    nam_convert.keep_preview = keep_preview
    nam_convert.classify_capture = classify_capture
    nam_convert.metadata_catalog = retention.metadata_catalog
    return nam_convert


def run_library_editor(exe, workspace, device, converter):
    from prepare_library import prepare_library
    exe, workspace = Path(exe), Path(workspace)
    package = prepare_library(exe, workspace / 'floor-library')
    catalog_path = default_library_path()
    run_dir = workspace / 'library-session'
    run_dir.mkdir(exist_ok=False)
    automatic_slots = True
    observe_ir_slot, observe_ir_exports = None, False
    workspace = run_dir / 'imports'
    workspace.mkdir()
    def catalog_changed(rows):
        if not detached.is_set():
            script.post({'type': 'libraryUpdate', 'payload': rows})
    conversion = configure_modules(package, workspace, catalog_path=catalog_path, on_catalog_change=catalog_changed)
    from tone_preview import TonePreview
    import ctypes

    pid = device.spawn([str(exe)], cwd=str(exe.parent))
    resumed = False
    connection_watch = None
    try:
        session = device.attach(pid)
        detached, ready = Event(), Event()
        from audition_diagnostics import Journal
        journal = Journal(run_dir / 'AUDITION-EVENTS.jsonl')
        export_trial = None
        def on_detached(reason, crash=None):
            try:
                journal.write('editor-detached', reason=str(reason), pid=pid)
                if crash is not None:
                    journal.write('native-crash', pid=getattr(crash, 'pid', pid))
            finally:
                try:
                    if export_trial: export_trial.close()
                finally: detached.set()
        session.on('detached', on_detached)
        modules = package / 'modules'
        from library_catalog import load as load_catalog, empty_catalog
        from nam_library_view import library_rows, imported_rows
        from library_dates import merge_dates
        catalog_file = catalog_path
        local_library = library_rows(merge_dates(load_catalog(catalog_file), catalog_file.parent / 'recovered-dates.json'), catalog_path=catalog_file) if catalog_file.exists() else []
        observer_source = ''
        if observe_ir_slot is not None or observe_ir_exports or automatic_slots:
            from native_export_trial import NativeExportTrial,NativeExportSeries
            if automatic_slots:
                from automatic_slot_service import AutomaticSlotService
                from uuid import uuid4
                export_trial=AutomaticSlotService(
                    lambda:load_catalog(catalog_file) if catalog_file.exists() else empty_catalog(),
                    run_dir/'ir-readback')
                export_config={'repeat':True,'request':uuid4().hex,'automatic':True}
            else:
                export_trial = (NativeExportSeries(lambda:load_catalog(catalog_file),run_dir/'ir-readback') if observe_ir_exports
                    else NativeExportTrial(load_catalog(catalog_file),run_dir / 'ir-readback',observe_ir_slot))
                export_config=export_trial.config
            (run_dir / 'IR-EXPORT-TEST.json').write_text(json.dumps({**export_config,
                'action':('Automatically verify occupied permanent slots through native HX Edit exports; refresh after reconnect'
                    if automatic_slots else 'Owner exports one slot at a time (1-127) to a new absolute WAV filename using HX Edit; repeat after reconnect'
                    if observe_ir_exports else 'Owner exports exactly this IR slot to this new WAV path using HX Edit'),
                'automaticExport':automatic_slots,'updatesLibrary':False},indent=2)+'\n',encoding='utf8')
            observer_source = (modules / 'native_export_observer.js').read_text(encoding='utf8')
            if automatic_slots:observer_source+='\n'+(modules/'automatic_slot_discovery.js').read_text(encoding='utf8')
            if (modules / 'library_slot_state.js').exists():
                observer_source += '\n' + (modules / 'library_slot_state.js').read_text(encoding='utf8')
                observer_source += '\nconnectNativeExportLibrary(' + json.dumps({**export_config,
                    'requireHostMonitor':(modules / 'device_connection_watch.py').exists()}) + ', namView);\n'
            else:
                observer_source += '\ninstallNativeExportObserver(' + json.dumps(export_trial.config) + ');\n'
        script = session.create_script('\n'.join((modules / name).read_text(encoding='utf-8')
                                                for name in ('nam_view.js', 'nam_import.js', 'tone_view.js'))
                                       + '\nconst namView = installNamView({}, ' + json.dumps(local_library)
                                       + ');\ninstallToneView();\n' + observer_source + 'send({type:"ready"});')
        def message(text, flags):
            return ctypes.windll.user32.MessageBoxW(None, str(text), 'HX Edit NAM library', flags)
        preview = TonePreview(script, workspace, converter, detached, message)
        from library_table_bridge import LibraryTableBridge
        table_exe = modules / 'build/NamLibrary.exe'
        table = LibraryTableBridge(table_exe, script, detached,
                                   on_classify=conversion.classify_capture) if table_exe.is_file() else None
        errors = []
        last_resize_status = [None]
        def receive(event, _data):
            if event['type'] == 'error':
                journal.write('hook-error', lineNumber=event.get('lineNumber'),
                              columnNumber=event.get('columnNumber'))
                errors.append('Native hook reported an error; stop this trial.')
                ready.set()
                return
            payload = event.get('payload', {})
            kind = payload.get('type')
            if kind in ('slotClearInventory','slotClearBlocked','slotClearContext','slotClearQueued','slotClearLifecycle'):
                journal.write('slot-clear',phase=kind,**{key:payload[key] for key in
                    ('complete','reason','references','slots','ready','baseline','depth','pendingReady',
                     'pendingKinds','lastEvent','notifications','ownerChanges','notificationErrors',
                     'otherEditorNotifications','unknownGlobals','unknownGlobalCount') if key in payload})
            if automatic_slots and kind=='automaticSlotPrepare':
                try:result=export_trial.prepare(payload)
                except (ValueError,OSError,KeyError,TypeError):result={'token':payload.get('token'),'error':'Preparation failed'}
                if not detached.is_set():script.post({'type':'automaticSlotPrepared','payload':result})
            if kind=='automaticSlotStatus':
                journal.write('automatic-slot-discovery',state=payload.get('state'),checked=payload.get('checked'),slot=payload.get('pending'))
            if kind == 'irExportObservation' and export_trial:
                evidence=export_trial.receive(payload)
                if evidence and not detached.is_set():
                    if automatic_slots:
                        if evidence['evidence']:
                            script.post({'type':'librarySlotEvidence','payload':{**evidence['evidence'],'automaticToken':evidence['token']}})
                        else:script.post({'type':'automaticSlotResult','payload':evidence})
                    else:script.post({'type':'librarySlotEvidence','payload':evidence})
                journal.write('ir-export-observation',phase=payload.get('stage'),reason=payload.get('reason'))
            if kind == 'librarySlotEvidenceResult':
                journal.write('library-slot-publication',accepted=payload.get('accepted'),slot=payload.get('slot'),
                    id=payload.get('request'))
            if kind == 'librarySlotInvalidated':
                journal.write('library-slots-invalidated',epoch=payload.get('epoch'))
            if kind == 'libraryConnectionGuard':
                journal.write('library-connection-guard',ready=payload.get('ready'),generation=payload.get('generation'))
            if table and kind in ('libraryTable', 'libraryResult', 'libraryResizeResult'):
                table.receive(payload)
            if kind == 'libraryTable':
                resize_status = payload.get('state', {}).get('resizeStatus')
                if resize_status is not None and resize_status != last_resize_status[0]:
                    last_resize_status[0] = resize_status
                    layout = resize_status.get('layout') or {}
                    journal.write('sidebar-layout', available=resize_status.get('available'),
                        reason=resize_status.get('reason'), width=resize_status.get('width'),
                        parentWidth=layout.get('parent', {}).get('w'),
                        libraryX=layout.get('library', {}).get('x'), libraryWidth=layout.get('library', {}).get('w'),
                        editorX=layout.get('editor', {}).get('x'), editorWidth=layout.get('editor', {}).get('w'),
                        rightAnchor=layout.get('right'), leftAnchor=layout.get('left'))
            if kind == 'libraryResizeResult':
                journal.write('sidebar-resize', **{key:payload.get(key) for key in
                    ('requested', 'width', 'changed', 'reason', 'detail', 'error')})
            if kind == 'toneTrace':
                journal.write('native-boundary', **{k: v for k, v in payload.items() if k != 'type'})
            if kind == 'ready': ready.set()
            if kind == 'libraryResolve':
                from library_load import resolve_capture
                try:
                    result = resolve_capture(catalog_file, payload.get('contentId'), workspace)
                except (ValueError, OSError, KeyError, StopIteration):
                    result = {'error': 'The kept capture could not be validated. No transfer was started.'}
                if not detached.is_set():
                    script.post({'type': 'libraryResolved', 'payload': {**result, 'session': payload.get('session')}})
            if kind == 'libraryResult':
                journal.write('library-result', slot=payload.get('savedSlot'),
                              reason='error' if payload.get('error') else 'success')
            if kind in ('toneStart', 'toneBounds', 'toneReply', 'toneCancel'): preview.receive(payload)
            if kind in ('convert', 'validateImport', 'stageTone'):
                try:
                    if kind == 'convert':
                        result = {'files': conversion.convert_batch(payload['files'], workspace, converter)}
                    elif kind == 'stageTone':
                        result = {'file': str(conversion.stage_tone_import(payload['file'], workspace, audition=payload['audition']))}
                    else:
                        result = {'profiles': conversion.validate_import(payload['files'], payload['firstSlot'], set(payload['occupied']), payload['namOnly'])}
                        result['libraryProfiles'] = imported_rows(merge_dates(conversion.metadata_catalog(), catalog_file.parent / 'recovered-dates.json'), payload['files'], payload['firstSlot'], result['profiles'], catalog_path=catalog_file)
                except Exception as error:
                    result = {'error': str(error)}
                if not detached.is_set(): script.post({'type': 'converted', 'payload': result})
        script.on('message', receive)
        script.load()
        if not ready.wait(5) or errors:
            raise RuntimeError('Hook initialization failed before editor resumed')
        if export_trial and (modules / 'device_connection_watch.py').exists():
            from device_connection_watch import ConnectionWatch,floor_presence
            def connection_changed(value):
                if not detached.is_set():
                    if observe_ir_exports or automatic_slots:export_trial.connection_changed()
                    script.post({'type':'hostDeviceChange','payload':value})
            connection_watch=ConnectionWatch(connection_changed,presence=floor_presence if automatic_slots else None)
            if not connection_watch.start():
                raise RuntimeError('Device notification guard unavailable; editor was not resumed')
        device.resume(pid)
        resumed = True
        # A detached GUI service, not a blocking shell/watch command. Keep imports
        # and callbacks alive until normal editor closure; no timeout kills.
        detached.wait()
        if preview.thread: preview.thread.join(timeout=2)
        if table: table.worker.join(timeout=4)
        (run_dir / 'EDITOR-DETACHED.json').write_text('{"detached": true}\n', encoding='utf-8')
    except BaseException:
        if not resumed:
            device.kill(pid)  # Suspended app has never opened a device/transfer.
        raise
    finally:
        if connection_watch:connection_watch.close()
