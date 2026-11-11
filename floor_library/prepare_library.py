"""Compose the Floor library from current upstream source and local resources.

No editor is launched, no device is opened, and no authentication is configured.
The prepared files are temporary; the source checkout and installation stay intact.
"""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from audition_diagnostics import patch_browser
from audition_import_patch import patch_import
from tone_lifecycle_patch import patch_preview
from preview_policy import patch_preview_retention, patch_card
from preview_cleanup import patch_preview_cleanup, patch_browser_escape
from preview_settings import patch_nam_settings
from nam_library_view import patch_view, patch_layout
from library_usability import patch_library_view, patch_library_layout
from library_table_patch import patch_table_view
from native_controls_patch import patch_display_helpers
from native_hook_guards import check_model_hook_pair
from slot_clear_patch import patch_clear_protection
from library_tab_order import patch_tab_view, patch_tab_corners
import native_export_static

EXE_HASH = 'f7faac4996164f9039c9ae3ca8d85cd135c5bceae8a511c8921a1244046cab66'
MODULES = ('nam_convert.py', 'nam_inventory.py', 'nam_view.js', 'nam_import.js',
           'tone3000.py', 'tone_preview.py', 'tone_browser.py', 'tone_browser.cs',
           'tone_view.js', 'tone_card.html')


def prepare_library(exe, output):
    exe, output = Path(exe), Path(output)
    if hashlib.sha256(exe.read_bytes()).hexdigest() != EXE_HASH:
        raise ValueError('The Floor library needs the pinned Windows HX Edit 3.82 executable')
    native_export_static.EDITOR_PATH = exe
    native_export_static.automatic_contract()
    output.mkdir(exist_ok=False)
    modules = output / 'modules'
    modules.mkdir()
    for name in MODULES:
        shutil.copyfile(ROOT / name, modules / name)
    # The native observer and state ledger are loaded by the host separately.
    for name in ('native_export_observer.js', 'automatic_slot_discovery.js',
                 'library_slot_state.js', 'device_connection_watch.py'):
        shutil.copyfile(HERE / name, modules / name)

    def change(name, transform):
        path = modules / name
        path.write_text(transform(path.read_text(encoding='utf-8')), encoding='utf-8')

    change('tone_preview.py', lambda s: patch_preview_cleanup(patch_preview_retention(patch_preview(s))))
    change('tone_card.html', patch_card)
    change('tone_browser.cs', lambda s: patch_browser_escape(patch_browser(s)))
    change('nam_import.js', patch_import)
    change('nam_view.js', lambda s: patch_display_helpers(patch_table_view(
        patch_library_view(patch_nam_settings(patch_view(s))))))
    resources = exe.parent / 'res'
    change('nam_view.js', lambda s: patch_clear_protection(s, resources / 'HelixModelDefs.bin'))
    patch_layout(resources)
    patch_library_layout(resources)
    patch_tab_corners(resources)
    change('nam_view.js', patch_tab_view)
    check_model_hook_pair((modules / 'nam_view.js').read_text(encoding='utf-8'))
    # This reviewed source combines upstream's async completion/staging with the
    # library workflow. Default upstream/macOS entry points are not replaced.
    shutil.copyfile(HERE / 'tone_view.js', modules / 'tone_view.js')
    build_library_helper(modules / 'build/NamLibrary.exe')
    build_browser_helper(modules)
    return output


def build_browser_helper(modules):
    # Reuse the upstream package's SDK instead of redownloading it per session.
    # Only our modified helper is compiled; the installed HX Edit stays intact.
    # Do not cache upstream tone_browser under its public module name: the
    # running session must import the patched helper from its temporary modules.
    spec = importlib.util.spec_from_file_location('_floor_browser_build', ROOT / 'tone_browser.py')
    browser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(browser)
    SDK_VERSION, SDK_ASSETS, browser_sdk = browser.SDK_VERSION, browser.SDK_ASSETS, browser.browser_sdk
    build = Path(modules) / 'build' / ('tone-browser-' + SDK_VERSION)
    build.mkdir(parents=True, exist_ok=True)
    cached = ROOT / 'build' / build.name
    if all((cached / Path(name).name).is_file() for name in SDK_ASSETS):
        for name in SDK_ASSETS:
            shutil.copyfile(cached / Path(name).name, build / Path(name).name)
    else:
        with browser_sdk() as sdk:
            for name in SDK_ASSETS:
                (build / Path(name).name).write_bytes(sdk.read(name))
    compiler = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework/v4.0.30319/csc.exe'
    references = ['System.Windows.Forms.dll', 'System.Drawing.dll', 'System.Web.Extensions.dll']
    references.extend(str(build / Path(name).name) for name in SDK_ASSETS[:2])
    result = subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x86', '/codepage:65001',
        '/out:' + str(build / 'ToneBrowser.exe'), *('/reference:' + value for value in references),
        str(Path(modules) / 'tone_browser.cs')], capture_output=True, text=True, errors='replace', timeout=45,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('Could not build the library browser helper:\n' + result.stdout + result.stderr)


def build_library_helper(destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    compiler = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework/v4.0.30319/csc.exe'
    result = subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x86',
        '/codepage:65001', '/out:' + str(destination), '/reference:System.Windows.Forms.dll',
        '/reference:System.Drawing.dll', '/reference:System.Web.Extensions.dll', str(HERE / 'NamLibrary.cs')],
        capture_output=True, text=True, errors='replace', timeout=45,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('Could not build the NAM library helper:\n' + result.stdout + result.stderr)
    return destination
