"""Prepare a private working copy of the original HX Edit 3.82 for macOS."""

from contextlib import contextmanager
from hashlib import file_digest
from pathlib import Path
import plistlib
import subprocess


EDITOR_BINARY = Path('Contents/MacOS/HX Edit')
EDITOR_HASH = '92d22d28d89936795108ca9a20f73de2364d00f3eaf06953fbd0d7562eccb119'
EDITOR_REQUIREMENT = ('anchor apple generic and certificate leaf[subject.OU] = "VL9JLAGDMF" '
                      'and identifier "com.line6.hxedit"')


def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def digest(path):
    with path.open('rb') as handle:
        return file_digest(handle, 'sha256').hexdigest()


@contextmanager
def editor_source(source, work):
    if source.suffix.lower() == '.app' and source.is_dir():
        yield source
        return
    if source.suffix.lower() == '.dmg' and source.is_file():
        volume = work / 'volume'
        volume.mkdir()
        run('/usr/bin/hdiutil', 'attach', '-quiet', '-readonly', '-nobrowse', '-mountpoint', volume, source)
        try:
            with editor_source(volume / 'HX Edit.pkg', work) as app:
                yield app
        finally:
            run('/usr/bin/hdiutil', 'detach', '-quiet', volume)
        return
    if source.suffix.lower() == '.pkg' and source.is_file():
        expanded = work / 'expanded'
        run('/usr/sbin/pkgutil', '--expand-full', source, expanded)
        apps = list(expanded.rglob('HX Edit.app'))
        if len(apps) != 1:
            raise ValueError('The installer must contain one HX Edit.app.')
        yield apps[0]
        return
    raise ValueError('Choose HX Edit 3.82 for macOS as an app, PKG or DMG.')


def prepare_editor(source, destination, work, *, model_names=None, hx_nam=None):
    from nam_catalog import nam_layers, nam_pedal, patch_catalog, patch_hx_catalog, patch_library, rename_stock_models

    with editor_source(source, work) as original:
        exe = original / EDITOR_BINARY
        if not exe.is_file() or digest(exe) != EDITOR_HASH:
            raise ValueError('The original executable does not match HX Edit 3.82 for macOS.')
        try:
            run('/usr/bin/codesign', '--verify', '--strict', '-R', '=' + EDITOR_REQUIREMENT, original)
        except subprocess.CalledProcessError as error:
            raise ValueError('The original HX Edit.app did not pass the Line 6 signature check.') from error
        run('/usr/bin/ditto', '--noqtn', original, destination)

    contents = destination / 'Contents'
    info = contents / 'Info.plist'
    plist = plistlib.loads(info.read_bytes())
    plist['CFBundleIdentifier'] = 'com.andreidevs.hxeditnam.editor'
    plist['CFBundleName'] = 'HX Edit NAM'
    plist['CFBundleDisplayName'] = 'HX Edit NAM'
    info.write_bytes(plistlib.dumps(plist))
    resources = contents / 'Resources'
    patch_catalog(resources, nam_layers(), nam_pedal())
    if hx_nam:
        patch_hx_catalog(resources, hx_nam)
    if model_names:
        rename_stock_models(resources, model_names)
    patch_library(resources, False)
    run('/usr/bin/codesign', '--force', '--sign', '-', destination)
    return destination / EDITOR_BINARY
