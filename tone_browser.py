"""Build and drive the WebView2 child window over private process pipes."""

from io import BytesIO
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
from threading import Event, Lock, Thread
from time import monotonic
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
from zipfile import ZipFile

from tone3000 import ToneError


ROOT = Path(__file__).resolve().parent
SDK_VERSION = '1.0.4314.35'
SDK_ASSETS = (
    'lib/net462/Microsoft.Web.WebView2.Core.dll',
    'lib/net462/Microsoft.Web.WebView2.WinForms.dll',
    'runtimes/win-x86/native/WebView2Loader.dll',
)


def prepare_browser():
    source = ROOT / 'tone_browser.cs'
    build = ROOT / 'build' / f'tone-browser-{SDK_VERSION}'
    exe = build / 'ToneBrowser.exe'
    if exe.is_file() and exe.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return exe

    package = 'microsoft.web.webview2'
    filename = f'{package}.{SDK_VERSION}.nupkg'
    cached = Path(os.environ.get('NUGET_PACKAGES', Path.home() / '.nuget' / 'packages'))
    cached = cached / package / SDK_VERSION / filename
    if cached.is_file():
        archive = cached.read_bytes()
    else:
        url = f'https://api.nuget.org/v3-flatcontainer/{package}/{SDK_VERSION}/{filename}'
        try:
            with urlopen(url, timeout=20) as response:
                archive = response.read(20 * 1024 * 1024 + 1)
        except OSError as error:
            raise ToneError('Could not download the WebView2 SDK from NuGet. Try opening TONE3000 again.') from error
        if len(archive) > 20 * 1024 * 1024:
            raise ToneError('The WebView2 SDK package exceeded the download limit.')

    build.mkdir(parents=True, exist_ok=True)
    with ZipFile(BytesIO(archive)) as sdk:
        for name in SDK_ASSETS:
            (build / Path(name).name).write_bytes(sdk.read(name))
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET' / 'Framework' / 'v4.0.30319' / 'csc.exe'
    references = ['System.Windows.Forms.dll', 'System.Drawing.dll', 'System.Web.Extensions.dll']
    references.extend(str(build / Path(name).name) for name in SDK_ASSETS[:2])
    temporary = build / 'ToneBrowser-new.exe'
    result = subprocess.run(
        [str(compiler), '/nologo', '/target:winexe', '/platform:x86', '/codepage:65001', f'/out:{temporary}',
         *(f'/reference:{assembly}' for assembly in references), str(source)],
        capture_output=True, text=True, errors='replace', timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise ToneError(f'Could not build the TONE3000 pane.\n\n{result.stdout.strip()}')
    temporary.replace(exe)
    return exe


class ToneBrowser:
    def __init__(self, parent, bounds, detached):
        exe = prepare_browser()
        profile = Path(os.environ['LOCALAPPDATA']) / 'HX Edit NAM' / 'TONE3000 WebView'
        profile.mkdir(parents=True, exist_ok=True)
        self.detached = detached
        self.closed = Event()
        self.ready = Event()
        self.actions = Queue()
        self.write_lock = Lock()
        self.error = None
        self.process = subprocess.Popen(
            [str(exe), str(parent), str(profile), *(str(bounds[key]) for key in ('x', 'y', 'w', 'h'))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, encoding='utf-8', errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.reader = Thread(target=self.read_events, daemon=True)
        self.reader.start()

    def read_events(self):
        with self.process.stdout as stdout:
            try:
                for line in stdout:
                    event = json.loads(line)
                    if event['type'] == 'ready':
                        self.ready.set()
                    elif event['type'] == 'action':
                        self.actions.put(event['value'])
                    elif event['type'] == 'error':
                        self.error = event['text']
            finally:
                self.closed.set()

    def is_set(self):
        return self.closed.is_set() or self.detached.is_set()

    def send(self, kind, **data):
        if self.is_set():
            return False
        try:
            with self.write_lock:
                self.process.stdin.write(json.dumps(dict(type=kind, **data)) + '\n')
                self.process.stdin.flush()
            return True
        except (OSError, ValueError):
            self.closed.set()
            return False

    def wait_ready(self):
        deadline = monotonic() + 15
        while not self.ready.wait(0.2) and not self.is_set() and monotonic() < deadline:
            pass
        if self.error:
            raise ToneError(self.error)
        if self.is_set():
            return False
        if not self.ready.is_set():
            raise ToneError('The TONE3000 pane did not start. Check the Microsoft Edge WebView2 Runtime.')
        return True

    def navigate(self, url):
        callback = parse_qs(urlsplit(url).query)['redirect_uri'][0]
        return self.send('navigate', url=url, callback=callback)

    def next_action(self):
        while not self.is_set():
            try:
                return self.actions.get(timeout=0.25)
            except Empty:
                pass
        return None

    def close(self):
        self.send('close')
        with self.write_lock:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait()
        self.reader.join(timeout=1)
