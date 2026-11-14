"""Package HX Edit NAM with the Windows x64 embeddable Python and WebView2 helper."""

import argparse
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from zipfile import ZipFile

from tone_browser import ROOT, SDK_ASSETS, browser_sdk, prepare_browser
from nam_sidebar import prepare_sidebar


PYTHON_VERSION = '3.13.17'
PYTHON_SHA256 = '9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38'
FIRMWARE_REV = 'de8ea773d05ef2dd85bd61a9ad4cba3417e3da0f'
APP_FILES = (
    'start-hx-edit-nam.pyw', 'nam_catalog.py', 'nam_convert.py', 'nam_import.js', 'nam_inventory.py',
    'nam_view.js', 'nam_library.py', 'library_card.html', 'tone3000.py', 'tone_browser.py', 'tone_card.html',
    'tone_preview.py', 'tone_view.js', 'nam_light.py', 'nam_modes.py',
    'floor_reference.py', 'floor_capture.py', 'nam_navigation.py',
    'navigation_host.py', 'floor_inventory.py', 'device_connection_watch.py',
    'library_slot_state.js', 'automatic_slot_discovery.js', 'native_export_observer.js',
    'nav_resources.py', 'nav_banner.js', 'target_binding.js',
    'nam_sidebar.py', 'nam_sidebar.js', 'NamLibrary.cs', 'floor_memory.py', 'capture_icons.py',
)
# Pictures the app's patches and the library read at run time (capture_icons, nam_catalog, nav_resources).
ASSET_FOLDERS = ('nam_capture_icons', 'nam_category_icons', 'nav_images')
FIRMWARE_FILES = (
    'LICENSE', 'tools/nam2ir.py', 'tools/nam_refine.py', 'tools/nam_pedal.py', 'tools/nam_v3_input.wav', 'tools/hx_ctl.py', 'tools/hx_dfu_reboot.py',
    'tools/sharc/nam_lite.py', 'tools/sharc/nam_pack.py', 'tools/sharc/sasm.py',
    'tools/sharc/ssim.py', 'tools/sharc/recips_table.json',
)


TRAINING = ('torch==2.15.0', 'numpy==2.5.4', 'scipy==1.18.1', 'pydantic==2.14.3', 'wavio==0.0.9', 'tqdm==4.70.1')
NAM_PACKAGE = 'neural-amp-modeler==0.13.0'


def add_runtime(destination):
    url = f'https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip'
    with urlopen(url, timeout=30) as response:
        archive = response.read(16 * 1024 * 1024 + 1)
    if sha256(archive).hexdigest() != PYTHON_SHA256:
        raise RuntimeError('The embeddable Python archive does not match the published checksum.')
    with ZipFile(BytesIO(archive)) as package:
        package.extractall(destination)
    (destination / 'python313._pth').write_text('python313.zip\n.\nLib/site-packages\n../app\n', encoding='ascii')
    subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '--only-binary=:all:', '--no-compile', '--no-deps',
         '--platform', 'win_amd64', '--python-version', '3.13', '--target', str(destination / 'Lib/site-packages'),
         'frida==17.5.2', 'msgpack==1.2.4'],
        check=True,
    )
    # Training (Lighter…, the import modes, the pedal converter): torch on the CPU and what helix-lt-nam's
    # nam_refine.py / nam_pedal.py use of neural-amp-modeler (its trainer and librosa are left out: nam_refine
    # stands in empty modules for them). About 700 MB unpacked.
    site = str(destination / 'Lib/site-packages')
    subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '--only-binary=:all:', '--no-compile', '--platform', 'win_amd64',
         '--python-version', '3.13', '--target', site, '--index-url', 'https://download.pytorch.org/whl/cpu',
         '--extra-index-url', 'https://pypi.org/simple', *TRAINING],
        check=True,
    )
    subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '--no-compile', '--no-deps', '--target', site, NAM_PACKAGE],
        check=True,
    )
    for stray in Path(site).glob('*.whl'):          # (pip leaves a wheel it fetched for another index)
        stray.unlink()


def package_windows(version, worktree=False):
    build = ROOT / 'build'
    helper = prepare_browser()
    sidebar = prepare_sidebar()
    package_name = f'HX-Edit-NAM-{version}-Windows-x64'
    with TemporaryDirectory(prefix='release-', dir=build) as folder:
        stage = Path(folder) / package_name
        app = stage / 'app'
        app.mkdir(parents=True)
        for name in APP_FILES:
            shutil.copyfile(ROOT / name, app / name)
        for name in ASSET_FOLDERS:
            shutil.copytree(ROOT / name, app / name)
        shutil.copyfile(sidebar, app / sidebar.name)
        shutil.copyfile(sidebar.with_suffix('.exe.sha256'), app / (sidebar.name + '.sha256'))
        shutil.copyfile(ROOT / 'README.md', stage / 'README.md')
        browser = app / 'build' / helper.parent.name
        browser.mkdir(parents=True)
        for name in (helper.name, *(Path(asset).name for asset in SDK_ASSETS)):
            shutil.copyfile(helper.parent / name, browser / name)
        with browser_sdk() as sdk:
            for name in ('LICENSE.txt', 'NOTICE.txt'):
                (browser / name).write_bytes(sdk.read(name))

        firmware = ROOT.parent / 'helix-lt-nam'
        for name in FIRMWARE_FILES:
            destination = stage / 'helix-lt-nam' / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if worktree:            # the checkout as it is, uncommitted changes included (a personal build)
                destination.write_bytes((firmware / name).read_bytes())
            else:
                destination.write_bytes(subprocess.check_output(
                    ['git', '-C', str(firmware), 'show', f'{FIRMWARE_REV}:{name}']))
        rev = subprocess.check_output(['git', '-C', str(firmware), 'rev-parse', 'HEAD'], text=True).strip() \
            if worktree else FIRMWARE_REV
        (stage / 'helix-lt-nam' / 'UPSTREAM.txt').write_text(
            f'https://github.com/andreidevs/helix-lt-nam/tree/{rev}' + (' + local changes' if worktree else '') + '\n',
            encoding='ascii')

        add_runtime(stage / 'runtime')
        compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework/v4.0.30319/csc.exe'
        subprocess.run(
            [str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/reference:System.Windows.Forms.dll',
             f'/out:{stage / "HX Edit NAM.exe"}', str(ROOT / 'windows-launcher.cs')],
            check=True,
        )
        archive = Path(shutil.make_archive(str(Path(folder) / package_name), 'zip', folder, package_name))
        output = build / archive.name
        archive.replace(output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version', help='Release version such as v0.1.0')
    parser.add_argument('--firmware-worktree', action='store_true',
                        help="take helix-lt-nam's tools from its checkout as it is, not from FIRMWARE_REV")
    args = parser.parse_args()
    if os.name != 'nt' or not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', args.version):
        parser.error('Run on Windows and specify a version such as v0.1.0.')
    print(package_windows(args.version, args.firmware_worktree))
