"""Start the local HX Edit NAM build and service the IR import converter."""

import argparse
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from threading import Event


ROOT = Path(__file__).resolve().parent
CONVERTER = ROOT.parent / 'helix-lt-nam' / 'tools' / 'nam2ir.py'
TITLE = 'HX Edit NAM'
TRACE = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'HX Edit NAM' / 'trace.log'


def message(text, flags):
    return ctypes.windll.user32.MessageBoxW(None, str(text), TITLE, flags)


def trace(payload):
    """Append editor diagnostics to TRACE; they hold no capture or preset names."""
    line = json.dumps({'time': datetime.now().isoformat(timespec='seconds'), **payload})
    print(line)
    try:
        TRACE.parent.mkdir(parents=True, exist_ok=True)
        with TRACE.open('a', encoding='utf-8') as log:
            log.write(line + '\n')
    except OSError:
        pass


def ask_firmware():
    """Ask which NAM firmware the unit runs (Enter: the last answer) and keep it in settings.json: it sets
    the NAM blocks of HX Edit's catalog and the import modes. Cancel keeps the last answer."""
    import nam_modes
    from nam_catalog import nam_layers, nam_pedal, save_setting
    current = 'pedal' if nam_pedal() else 'layers' if nam_layers() else 'plain'
    try:
        key = nam_modes.choose_firmware(current)
    except OSError as error:                      # no task dialog: keep the last answer
        print(f'Could not ask for the firmware: {error}')
        return current
    key = key or current
    save_setting('namPedal', key == 'pedal')
    save_setting('namLayers', key in ('pedal', 'layers'))
    return key


def choose_and_train(payload, workspace):
    """The files to convert for an import: as chosen, or with the captures replaced by trained copies
    (the mode chooser, on a firmware with the lighter NAM blocks and an import that asks). None: cancelled."""
    from nam_catalog import nam_layers, nam_pedal
    files = list(payload['files'])
    captures = [f for f in files if f.lower().endswith('.nam')]
    if not (payload.get('ask') and captures and nam_layers()):
        return files
    import nam_modes
    owner = int(payload.get('owner') or 0)
    key = nam_modes.choose_mode([Path(c).stem for c in captures], owner, nam_pedal())
    if key is None:
        return None
    if key == 'original':
        return files
    trained = nam_modes.train(captures, key, workspace, CONVERTER.with_name('nam_refine.py'), owner)
    if trained is None:
        return None
    swap = dict(zip(captures, trained))
    return [swap.get(f, f) for f in files]


def run_editor(exe, workspace, device, profiles, firmware='plain', *, floor_reference=False,
               tone_window='embedded', navigation=False, client_id='', library=None):
    from functools import partial
    import nam_modes
    from nam_catalog import ir_parameters, save_setting, sidebar_width
    from nam_convert import convert_batch, stage_tone_import, validate_import
    from nam_library import Library
    from tone_preview import TonePreview
    from tone_browser import ToneBrowser
    from floor_reference import FLOOR_REFERENCE_OPTIONS
    from floor_capture import validate_floor_capture_files

    if navigation and not floor_reference:
        raise ValueError('Navigation requires the Floor reference profile.')
    library = library if library is not None else Library()
    if navigation:
        from floor_inventory import prime_library
        from navigation_host import NavigationHost
        from nam_sidebar import prepare_sidebar
        from uuid import uuid4
        # Fail before an editor/device session starts if its library presentation
        # cannot be built. Packaged builds include the matching compiled helper.
        prepare_sidebar()
        # Offline conversion precedes spawning HX Edit, never its native event callback.
        prime_library(library, CONVERTER, Path(workspace) / 'library-index', timeout=120)
    host = None
    preview = None
    sidebar = None
    memory = None
    pid = device.spawn([str(exe)], cwd=str(exe.parent))
    try:
        session = device.attach(pid)
        detached = Event()
        ready = Event()
        errors = []
        session.on('detached', lambda *_: detached.set())
        imports = (ROOT / 'nam_import.js').read_text(encoding='utf-8')
        view = (ROOT / 'nam_view.js').read_text(encoding='utf-8')
        tone = (ROOT / 'tone_view.js').read_text(encoding='utf-8')
        options = dict(FLOOR_REFERENCE_OPTIONS) if floor_reference else {}
        before, initialize, after = '', '', ''
        if navigation:
            options.update(navigation=True, nativeDiscovery=True, payloadMetadata=library.payload_metadata())
            # A preview left on a block by a closed editor, and the slots already read back.
            from floor_memory import FloorMemory
            memory = FloorMemory(library.folder)
            options.update(leftoverPreview=memory.preview(), rememberedSlots=memory.slots())
            # The NAM block category exists in this copy's catalog (nam_catalog.patch_nam_category).
            options.update(namCategory=(exe.parent / 'res' / 'icons_category' / 'FX_HX_Category_NAM_Amp.png').is_file())
            # Read-only research probes: only in a copy prepared with this marker file.
            options.update(namProbe=(exe.parent / 'res' / 'nam-probe.flag').is_file())
            before = '\n'.join((ROOT / name).read_text(encoding='utf-8') for name in
                ('library_slot_state.js', 'automatic_slot_discovery.js', 'native_export_observer.js',
                 'nav_banner.js', 'target_binding.js', 'nam_sidebar.js')) + '\n'
            config = dict(request=uuid4().hex, repeat=True, automatic=True, requireHostMonitor=True)
            # Validate original native bytes before namView attaches to shared
            # entry points. The callback runs only after toneView exists and the
            # suspended editor resumes; installing it does not open the browser.
            initialize = '\nconst navigationBanner = installNavigationBanner({openBrowser: (window, options) => toneView.openBrowser(window, options)});\n'
            after = (f'\nconnectNativeExportLibrary({json.dumps(config)}, namView);'
                     '\nnamView.setLibraryOpener(window => toneView.openBrowser(window, {library:true, dockPanel:navigationBanner.dockPanel()}));'
                     '\ninstallNamSidebar(namView);')
        script = session.create_script(
            f"{before}{view}\n{imports}\n{tone}\n{initialize}const namView = installNamView({json.dumps(profiles)}, {sidebar_width()}, {json.dumps(ir_parameters(exe.parent / 'res'))}, {json.dumps(options)});"
            f"\nconst targetBinding = {'installTargetBinding()' if navigation else 'null'};"
            f"\nconst toneView = installToneView({json.dumps(nam_modes.tiles(firmware))}, {json.dumps(options)}, targetBinding);{after}\nsend({{ type: 'ready' }});"
        )
        if navigation:
            host = NavigationHost(script, workspace, library, detached)
        preview = TonePreview(script, workspace, CONVERTER, detached, message, library=library,
                              client_id=client_id, browser_factory=partial(ToneBrowser, mode=tone_window),
                              payload_callback=(lambda _: host.metadata_changed()) if host else None,
                              trace_callback=trace if navigation else None)
        if navigation:
            from nam_sidebar import NamSidebar
            def sidebar_action(action, identity):
                if not detached.is_set():
                    script.post({'type': 'namSidebarAction', 'payload': {'action': action, 'id': identity}})
            sidebar = NamSidebar(script, library, detached, action_handler=sidebar_action, trace=trace)
            preview.sidebar_status = sidebar.status

        def on_message(event, _data):
            if event['type'] == 'error':
                errors.append(event.get('description', 'IR import hook failed.'))
                trace({'type': 'nativeScriptError', 'description': errors[-1],
                       'stack': event.get('stack', ''), 'line': event.get('lineNumber')})
                ready.set()
                return
            payload = event.get('payload', {})
            if memory:
                memory.receive(payload)
            if sidebar:
                sidebar.receive(payload)
            if host:
                host.receive(payload)
                if payload.get('type') == 'floorInventory':
                    preview.inventory_changed()
            if payload.get('type') == 'ready':
                ready.set()
            if payload.get('type') in {'toneTrace', 'navigationBanner', 'automaticSlotStatus', 'floorInventoryPresentation',
                                       'libraryConnectionGuard', 'librarySlotEvidenceResult'}:
                trace(payload)
            if payload.get('type') == 'irExportObservation' and payload.get('stage') in {'invalidated', 'discarded', 'slot-changed'}:
                # Structural diagnostics only: no capture names, paths or tokens.
                trace({'type': 'slotVerificationEvent', 'stage': payload.get('stage'),
                       'reason': payload.get('reason'), 'slot': payload.get('slot')})
            if payload.get('type') == 'floorInventory':
                trace({'type': 'floorInventory', 'epoch': payload.get('epoch'),
                       'state': payload.get('state'), 'verifiedSlots': len(payload.get('entries', []))})
            if payload.get('type') == 'sidebarWidth':
                try:
                    save_setting('sidebarWidth', int(payload['width']))
                except (OSError, TypeError, ValueError, KeyError) as error:
                    print(f'Could not save the sidebar width: {error}')
            if payload.get('type') in {'toneStart', 'toneBounds', 'toneReply', 'toneDismiss', 'toneTarget', 'toneFocus', 'toneAction'}:
                preview.receive(payload)
            if payload.get('type') in {'convert', 'validateImport', 'stageTone'}:
                try:
                    if payload['type'] == 'convert':
                        # The Floor reference has only the original engine. A
                        # different editor's saved LT training preference must
                        # not silently alter this profile's imports.
                        files = list(payload['files']) if floor_reference else choose_and_train(payload, workspace)
                        result = {'cancelled': True} if files is None else \
                            {'files': convert_batch(files, workspace, CONVERTER)}
                        if floor_reference and files is not None:
                            validate_floor_capture_files(result['files'])
                        for index, file in enumerate(payload['files'] if files is not None else []):
                            if file.lower().endswith('.nam'):
                                try:
                                    identity = library.add(file)
                                    if host:
                                        library.register_payload(identity, result['files'][index])
                                        host.metadata_changed()
                                except (OSError, ValueError) as error:
                                    print(f'Could not add {file} to the NAM library: {error}')
                    elif payload['type'] == 'stageTone':
                        if floor_reference:
                            validate_floor_capture_files([payload['file']])
                        staged = stage_tone_import(payload['file'], workspace, audition=payload['audition'])
                        result = {'file': str(staged)}
                    else:
                        if floor_reference:
                            validate_floor_capture_files(payload['files'])
                        imported = validate_import(payload['files'], payload['firstSlot'],
                                                   set(payload['occupied']), payload['namOnly'])
                        result = {'profiles': imported}
                except Exception as error:
                    result = {'error': str(error)}
                if not detached.is_set():
                    script.post({'type': 'converted', 'payload': result})

        script.on('message', on_message)
        script.load()
        if not ready.wait(5) or errors:
            raise RuntimeError(errors[0] if errors else 'HX Edit did not initialize the IR import hook.')
        if host:
            host.start()
        device.resume(pid)
        detached.wait()
        if preview.thread:
            preview.thread.join(timeout=2)
    except BaseException:
        try:
            device.kill(pid)
        except Exception:
            pass
        raise
    finally:
        try:
            if host:
                host.close()
        finally:
            try:
                if sidebar:
                    sidebar.close()
            finally:
                if preview:
                    preview.close_diagnostics()


def main():
    import frida
    from nam_catalog import prepare_copy
    from nam_inventory import HX_NAM_DEVICES, scan_profiles
    from floor_reference import require_plain_catalog, startup_profiles

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lt-nam', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--hx-nam', choices=HX_NAM_DEVICES, help='Confirm the device runs NAM A2 Lite on preset ID 555.')
    parser.add_argument('--floor-reference', action='store_true',
                        help='Original Floor A2 Lite profile: native editor USB only and preview slot 128 reserved.')
    parser.add_argument('--tone-window', choices=('embedded', 'floating'), default='embedded',
                        help='TONE3000 browser presentation; floating leaves the native controls visible.')
    parser.add_argument('--navigation', action='store_true',
                        help='Experimental Floor header browser and verified native slot discovery.')
    parser.add_argument('--nam-category', action='store_true',
                        help='Experimental: NAM Amp / Amp + cab / Pedal / Other categories in the block selector.')
    args = parser.parse_args()

    if args.navigation and not args.floor_reference:
        parser.error('--navigation requires --floor-reference')
    if args.nam_category and not args.navigation:
        parser.error('--nam-category requires --navigation')

    if args.floor_reference:
        from nam_catalog import nam_layers, nam_pedal
        require_plain_catalog(layers=nam_layers(), pedal=nam_pedal())
        if args.hx_nam:
            parser.error('--floor-reference and --hx-nam are mutually exclusive')

    device = frida.get_local_device()
    if any(p.name.lower() in {'hx edit.exe', 'hx edit nam.exe'} for p in device.enumerate_processes()):
        raise RuntimeError('Close the open HX Edit window, then start HX Edit NAM again.')
    if not CONVERTER.is_file():
        raise FileNotFoundError('Keep the helix-lt-nam checkout next to hx-edit-nam for the built-in converter.')

    profiles, scan_error = startup_profiles(floor_reference=args.floor_reference,
        scan=scan_profiles, converter_directory=CONVERTER.parent, hx_nam=args.hx_nam)

    firmware = 'plain' if args.hx_nam or args.floor_reference else ask_firmware()
    (ROOT / 'build').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nam-', dir=ROOT / 'build', ignore_cleanup_errors=True) as workspace:
        exe = prepare_copy(workspace, scan_error, hx_nam=args.hx_nam)
        if args.navigation:
            from nav_resources import patch_navigation_resources, patch_library_navigation
            patch_navigation_resources(exe.parent)
            patch_library_navigation(exe.parent)
        if args.nam_category:
            from nam_catalog import patch_nam_category
            patch_nam_category(exe.parent / 'res')
        run_editor(exe, workspace, device, profiles, firmware, floor_reference=args.floor_reference,
                   tone_window=args.tone_window, navigation=args.navigation)


if __name__ == '__main__':
    try:
        main()
    except ModuleNotFoundError as error:
        message(f'{error}\n\nInstall the launcher dependencies with: python -m pip install frida msgpack', 0x10)
    except Exception as error:
        message(error, 0x10)
