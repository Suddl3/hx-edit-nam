"""Launch the local Apple Silicon HX Edit NAM build."""

from contextlib import contextmanager
import argparse
import fcntl
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
from threading import Event, Thread
from time import monotonic, sleep

from prepare_editor_macos import EDITOR_BINARY, prepare_editor


ROOT = Path(__file__).resolve().parent
CONVERTER = ROOT / 'helix-lt-nam' / 'tools' / 'nam2ir.py'
TITLE = 'HX Edit NAM'
APP_SUPPORT = Path.home() / 'Library' / 'Application Support' / TITLE


def dialog(text, confirm=False):
    buttons = 'buttons {"Cancel", "Continue"} default button "Continue" cancel button "Cancel"' if confirm else 'as critical'
    result = subprocess.run(
        ['/usr/bin/osascript', '-e', 'on run argv',
         '-e', f'display alert "{TITLE}" message (item 1 of argv) {buttons}', '-e', 'end run',
         '--', str(text)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


@contextmanager
def single_instance():
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    with (APP_SUPPORT / 'editor.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Close HX Edit NAM before starting it again.') from None
        if subprocess.run(['/usr/bin/pgrep', '-xq', 'HX Edit']).returncode == 0:
            raise RuntimeError('Close the open HX Edit window, then start HX Edit NAM again.')
        yield


def working_editor(source, workspace):
    bundled = ROOT / 'HX Edit.app' / EDITOR_BINARY
    if source is None:
        if bundled.is_file():
            return bundled
        installed = Path('/Applications/Line6/HX Edit.app')
        installer = Path.home() / 'Downloads' / 'HXEdit.dmg'
        if installed.is_dir():
            source = installed
        elif installer.is_file():
            source = installer
        else:
            raise FileNotFoundError('Install Line 6 HX Edit 3.82 in /Applications/Line6 or put the official '
                                    'HXEdit.dmg in Downloads. To use another path, pass --hx-edit-source.')
    with tempfile.TemporaryDirectory(prefix='source-', dir=workspace) as work:
        return prepare_editor(source.expanduser().resolve(), workspace / 'HX Edit.app', Path(work))


def run_editor(editor, workspace, profiles, scan_failed):
    import frida
    from nam_convert import convert_batch, stage_tone_import, validate_import
    from tone_browser_macos import MacToneBrowser
    from tone_preview import TonePreview

    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        address, port = server.getsockname()
    token = secrets.token_hex(32)
    gadget = workspace / 'gadget.dylib'
    shutil.copyfile(ROOT / 'gadget.dylib', gadget)
    gadget.with_suffix('.config').write_text(json.dumps({'interaction': {
        'type': 'listen', 'address': address, 'port': port, 'token': token,
    }}), encoding='utf-8')
    manager = frida.get_device_manager()
    endpoint = f'{address}:{port}'
    browser = ROOT / 'tone-browser-macos.dylib'
    process = subprocess.Popen([str(editor)], cwd=editor.parent,
                               env={**os.environ, 'DYLD_INSERT_LIBRARIES': f'{browser}:{gadget}'})
    device = None
    try:
        device = manager.add_remote_device(endpoint, token=token)
        deadline = monotonic() + 10
        while True:
            try:
                session = device.attach(process.pid)
                break
            except (frida.ServerNotRunningError, frida.TransportError) as error:
                if process.poll() is not None or monotonic() >= deadline:
                    raise RuntimeError('Could not initialize HX Edit NAM.') from error
                sleep(0.1)

        ready, detached = Event(), Event()
        session.on('detached', lambda *_: detached.set())
        errors = []
        source = (ROOT / 'nam_macos.js').read_text(encoding='utf-8')
        tone = (ROOT / 'tone_macos.js').read_text(encoding='utf-8')
        script = session.create_script(
            "new NativeFunction(Module.getGlobalExportByName('unsetenv'), 'int', ['pointer'])"
            "(Memory.allocUtf8String('DYLD_INSERT_LIBRARIES'));\n"
            f"{source}\n{tone}\ninstallToneMac(installNamMac({json.dumps(profiles)}, {json.dumps(scan_failed)}));\n"
            "send({type: 'ready'});"
        )
        key = os.environ.get('T3K_PUBLISHABLE_KEY', '').strip()
        if not key:
            try:
                key = (APP_SUPPORT / 'tone3000-publishable-key').read_text(encoding='utf-8').strip()
            except OSError:
                pass

        def make_browser(window, bounds, disconnected):
            return MacToneBrowser(script, preview.session, window, bounds, disconnected)

        preview = TonePreview(script, workspace, CONVERTER, detached, lambda text, _: dialog(text),
                              browser_factory=make_browser, client_id=key)

        def receive(event, _data):
            if event['type'] == 'error':
                detail = event.get('stack', event.get('description', 'HX Edit NAM hook failed.'))
                errors.append(detail)
                print(detail, file=sys.stderr, flush=True)
                ready.set()
                return
            payload = event.get('payload', {})
            kind = payload.get('type')
            if kind == 'ready':
                ready.set()
            elif kind in {'toneStart', 'toneBounds', 'toneReply'}:
                preview.receive(payload)
            elif kind == 'toneBrowserEvent':
                if preview.browser:
                    preview.browser.receive(payload['event'])
            elif kind in {'importError', 'toneError'}:
                Thread(target=dialog, args=(payload['message'],), daemon=True).start()
            elif kind in {'convert', 'validateImport', 'stageTone'}:
                try:
                    if kind == 'convert':
                        result = {'files': convert_batch(payload['files'], workspace, CONVERTER)}
                    elif kind == 'stageTone':
                        result = {'file': str(stage_tone_import(payload['file'], workspace))}
                    else:
                        result = {'profiles': validate_import(payload['files'], payload['firstSlot'],
                                                               set(payload['occupied']), payload['namOnly'])}
                except Exception as error:
                    result = {'error': str(error)}
                if process.poll() is None:
                    script.post({'type': 'converted', 'payload': result})

        script.on('message', receive)
        script.load()
        if not ready.wait(5) or errors:
            raise RuntimeError(errors[0] if errors else 'HX Edit NAM did not initialize its hooks.')
        device.resume(process.pid)
        code = process.wait()
        detached.set()
        if preview.thread:
            preview.thread.join(timeout=2)
        if code:
            raise RuntimeError(errors[0] if errors else f'HX Edit closed with exit status {code}.')
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if device is not None:
            manager.remove_remote_device(endpoint)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lt-nam', action='store_true', help='Confirm the Helix LT has the NAM firmware.')
    parser.add_argument('--hx-edit-source', type=Path, help='Local HX Edit 3.82 app, PKG or DMG')
    args = parser.parse_args()
    if not args.lt_nam and not dialog(
        'Is NAM A2 Lite already available under Amp > NAM on your Helix LT?', confirm=True,
    ):
        return
    from nam_inventory_macos import scan_profiles

    with single_instance(), tempfile.TemporaryDirectory(prefix='hx-edit-nam-') as work:
        workspace = Path(work)
        editor = working_editor(args.hx_edit_source, workspace)
        try:
            profiles = scan_profiles(CONVERTER.parent, editor.with_name('libusb-1.0.0.dylib'))
            scan_failed = False
        except Exception as error:
            print(f'Could not scan Helix LT NAM profiles: {error}', file=sys.stderr, flush=True)
            profiles, scan_failed = {}, True
        run_editor(editor, workspace, profiles, scan_failed)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(error, file=sys.stderr)
        dialog(error)
        sys.exit(1)
