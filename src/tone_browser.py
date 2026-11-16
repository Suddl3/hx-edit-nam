"""Build and drive the embedded or floating WebView2 helper over private pipes."""

from io import BytesIO
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import subprocess
import struct
from threading import Event, Lock, Thread, current_thread
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
WINDOW_MODES = ('embedded', 'floating')


def browser_command(exe, parent, profile, bounds, window_mode='embedded'):
    """Keep presentation explicit; a detached process event is not a window mode."""
    if window_mode not in WINDOW_MODES:
        raise ValueError('Unknown TONE3000 window mode.')
    return [str(exe), str(parent), str(profile),
            *(str(bounds[key]) for key in ('x', 'y', 'w', 'h')), window_mode]


def browser_sdk():
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
    return ZipFile(BytesIO(archive))


def write_browser_icon(executable, destination):
    """Copy the stock PE's icon resources to ICO without executing/loading it."""
    data = Path(executable).read_bytes()

    def take(offset, size):
        if offset < 0 or size < 0 or offset + size > len(data):
            raise ValueError('Invalid executable icon resource')
        return data[offset:offset + size]

    def number(offset, size=4):
        return int.from_bytes(take(offset, size), 'little')

    pe = number(0x3c)
    if take(0, 2) != b'MZ' or take(pe, 4) != b'PE\0\0':
        raise ValueError('Expected a local Windows executable for the browser icon')
    optional, count = pe + 24, number(pe + 6, 2)
    magic = number(optional, 2)
    if magic not in (0x10b, 0x20b):
        raise ValueError('Unsupported executable icon format')
    section = optional + number(pe + 20, 2)
    sections = [struct.unpack('<IIII', take(section + index * 40 + 8, 16)) for index in range(count)]

    def raw(rva, size=1):
        for _, address, length, pointer in sections:
            if address <= rva and rva + size <= address + length:
                return pointer + rva - address
        raise ValueError('Icon resource is outside executable sections')

    resources = raw(number(optional + (96 if magic == 0x10b else 112) + 16))

    def entries(relative):
        directory = resources + relative
        count = number(directory + 12, 2) + number(directory + 14, 2)
        if count > 4096:
            raise ValueError('Invalid icon resource directory')
        return [struct.unpack('<II', take(directory + 16 + i * 8, 8)) for i in range(count)]

    def leaves(kind):
        result = {}
        branch = next((target for name, target in entries(0) if name == kind), None)
        if branch is None or not branch & 0x80000000:
            return result
        for identity, languages in entries(branch & 0x7fffffff):
            if identity & 0x80000000 or not languages & 0x80000000:
                continue
            for _, leaf in entries(languages & 0x7fffffff):
                if leaf & 0x80000000:
                    continue
                entry = resources + leaf
                address, size = number(entry), number(entry + 4)
                result[identity] = take(raw(address, size), size)
                break
        return result

    groups, images = leaves(14), leaves(3)  # RT_GROUP_ICON and RT_ICON.
    if not groups:
        raise ValueError('The local HX Edit executable has no application icon')
    group = groups[min(groups)]
    if len(group) < 6 or group[:4] != b'\0\0\1\0':
        raise ValueError('Invalid application icon group')
    count = int.from_bytes(group[4:6], 'little')
    if not 0 < count <= 256 or len(group) != 6 + count * 14:
        raise ValueError('Invalid application icon entries')
    directory, payload = bytearray(group[:6]), bytearray()
    for index in range(count):
        entry = group[6 + index * 14:20 + index * 14]
        identity = int.from_bytes(entry[12:14], 'little')
        image = images.get(identity)
        if image is None or len(image) != int.from_bytes(entry[8:12], 'little'):
            raise ValueError('Application icon image is missing')
        directory.extend(entry[:12] + struct.pack('<I', 6 + count * 16 + len(payload)))
        payload.extend(image)
    destination = Path(destination)
    destination.write_bytes(directory + payload)
    return destination


def prepare_browser():
    source = ROOT / 'tone_browser.cs'
    build = ROOT / 'build' / f'tone-browser-{SDK_VERSION}'
    exe = build / 'ToneBrowser.exe'
    if exe.is_file() and (not source.is_file() or exe.stat().st_mtime_ns >= source.stat().st_mtime_ns):
        return exe
    build.mkdir(parents=True, exist_ok=True)
    with browser_sdk() as sdk:
        for name in SDK_ASSETS:
            (build / Path(name).name).write_bytes(sdk.read(name))
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET' / 'Framework' / 'v4.0.30319' / 'csc.exe'
    references = ['System.Windows.Forms.dll', 'System.Drawing.dll', 'System.Web.Extensions.dll']
    references.extend(str(build / Path(name).name) for name in SDK_ASSETS[:2])
    icon = build / 'ToneBrowser.ico'
    stock = ROOT / 'build' / 'HX Edit' / 'HX Edit.exe'
    if stock.is_file():
        write_browser_icon(stock, icon)
    if not icon.is_file():
        raise ToneError('The local HX Edit application icon is unavailable. Rebuild the prepared editor copy.')
    temporary = build / 'ToneBrowser-new.exe'
    result = subprocess.run(
        [str(compiler), '/nologo', '/target:winexe', '/platform:x86', '/codepage:65001', f'/out:{temporary}',
         f'/win32icon:{icon}', f'/resource:{icon},ToneBrowser.ico',
         *(f'/reference:{assembly}' for assembly in references), str(source)],
        capture_output=True, text=True, errors='replace', timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise ToneError(f'Could not build the TONE3000 pane.\n\n{result.stdout.strip()}')
    temporary.replace(exe)
    return exe


class ToneBrowser:
    startup_error = 'The TONE3000 pane did not start. Check the Microsoft Edge WebView2 Runtime.'
    queue_limit = 64
    write_timeout = 2.0
    close_timeout = 3.0
    terminate_timeout = 1.0
    kill_timeout = 1.0
    monitor_interval = 0.05
    join_timeout = 0.1

    def __init__(self, parent, bounds, detached, *, mode='embedded'):
        if mode not in WINDOW_MODES:
            raise ValueError('Unknown TONE3000 window mode.')
        self.window_mode = mode
        exe = prepare_browser()
        profile = Path(os.environ['LOCALAPPDATA']) / 'HX Edit NAM' / 'TONE3000 WebView'
        profile.mkdir(parents=True, exist_ok=True)
        self.detached = detached
        self.process = subprocess.Popen(
            browser_command(exe, parent, profile, bounds, mode),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, encoding='utf-8', errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self._initialize_io()
        self.reader = Thread(target=self.read_events, daemon=True)
        self.writer = Thread(target=self._write_commands, daemon=True)
        self.monitor = Thread(target=self._monitor_process, daemon=True)
        self.reader.start()
        self.writer.start()
        self.monitor.start()

    def _initialize_io(self):
        self.closed = Event()
        self.ready = Event()
        self.actions = Queue()
        self.error = None
        self.close_reason = None
        self._commands = Queue(maxsize=self.queue_limit)
        self._io_lock = Lock()
        self._io_wake = Event()
        self._shutdown_done = Event()
        self._closing = False
        self._close_deadline = None
        self._write_started = None

    def _discard_commands_locked(self):
        while True:
            try:
                self._commands.get_nowait()
            except Empty:
                return

    def _fail_locked(self, message):
        if not self.error:
            self.error = message
            self.actions.put(None)  # Wake a worker waiting for its next action.
        self.closed.set()
        self._discard_commands_locked()
        self._io_wake.set()

    def _fail(self, message):
        with self._io_lock:
            self._fail_locked(message)

    def _write_commands(self):
        try:
            while not self.is_set():
                try:
                    line, closing = self._commands.get(timeout=self.monitor_interval)
                except Empty:
                    continue
                with self._io_lock:
                    if self.is_set():
                        break
                    self._write_started = monotonic()
                self._io_wake.set()
                # Only this daemon thread ever touches stdin. A blocked pipe
                # cannot block Frida's callback thread or the close caller.
                self.process.stdin.write(line)
                self.process.stdin.flush()
                with self._io_lock:
                    self._write_started = None
                if closing:
                    break
        except (OSError, ValueError):
            if not self.closed.is_set():
                self._fail('TONE3000 browser communication failed. Pending browser commands were cancelled; '
                           'delivery of the current command is unconfirmed. Check HX Edit before retrying.')
        finally:
            # Closing a TextIOWrapper can itself wait on its write lock. Keep
            # this on the daemon writer, never on callback/shutdown threads.
            try:
                self.process.stdin.close()
            except (OSError, ValueError):
                pass

    def _stop_process(self):
        """Called only by the watchdog; every process wait is bounded."""
        if self.process.poll() is not None:
            return
        try:
            self.process.terminate()
        except OSError:
            pass
        try:
            self.process.wait(timeout=self.terminate_timeout)
            return
        except (subprocess.TimeoutExpired, OSError):
            pass
        try:
            self.process.kill()
        except OSError:
            pass
        try:
            self.process.wait(timeout=self.kill_timeout)
        except (subprocess.TimeoutExpired, OSError):
            self._fail('TONE3000 browser shutdown could not be confirmed. Close its window before retrying.')

    def _monitor_process(self):
        try:
            while self.process.poll() is None:
                now = monotonic()
                with self._io_lock:
                    if self._write_started is not None and now - self._write_started >= self.write_timeout:
                        self._fail_locked('TONE3000 browser stopped responding. Pending browser commands were '
                                          'cancelled; delivery of the current command is unconfirmed. '
                                          'Check HX Edit before retrying.')
                    closing_expired = self._close_deadline is not None and now >= self._close_deadline
                    if closing_expired and not self.closed.is_set():
                        self._fail_locked('TONE3000 browser did not close in time. Its helper was stopped; '
                                          'check HX Edit before retrying.')
                if self.is_set() or closing_expired:
                    break
                self._io_wake.wait(self.monitor_interval)
                self._io_wake.clear()
            self._stop_process()
        finally:
            with self._io_lock:
                self.closed.set()
                self._discard_commands_locked()
            self._shutdown_done.set()

    def read_events(self):
        with self.process.stdout as stdout:
            try:
                for line in stdout:
                    event = json.loads(line)
                    if event['type'] == 'ready':
                        self.ready.set()
                    elif event['type'] == 'action':
                        action = event['value']
                        if isinstance(action, dict) and isinstance(action.get('close'), str):
                            self.close_reason = action['close']
                        self.actions.put(action)
                    elif event['type'] == 'presentation':
                        mode = event.get('mode')
                        if mode not in WINDOW_MODES:
                            raise ValueError('Invalid browser presentation mode')
                        self.window_mode = mode
                    elif event['type'] == 'error':
                        self._fail(event['text'])
            except (OSError, ValueError, KeyError):
                self._fail('TONE3000 browser communication ended unexpectedly. Check HX Edit before retrying.')
            finally:
                if self.close_reason is None:
                    self.close_reason = 'helper-disconnected'
                self.closed.set()
                self._io_wake.set()

    def is_set(self):
        return self.closed.is_set() or self.detached.is_set()

    def send(self, kind, **data):
        if self.is_set() or self._closing:
            return False
        # Floating panes retain fresh dock geometry, but only the helper decides
        # whether to apply it. Switching presentation never restarts the session.
        if kind == 'presentation' and data.get('mode') not in WINDOW_MODES:
            raise ValueError('Unknown TONE3000 window mode.')
        line = json.dumps(dict(type=kind, **data)) + '\n'
        with self._io_lock:
            if self.is_set() or self._closing:
                return False
            try:
                self._commands.put_nowait((line, kind == 'close'))
            except Full:
                self._fail_locked('TONE3000 browser command queue filled. Browser communication stopped and '
                                  'pending commands were cancelled. Check HX Edit before retrying.')
                return False
            if kind == 'close':
                self._closing = True
                self._close_deadline = monotonic() + self.close_timeout
        self._io_wake.set()
        return True  # Accepted in order, not an acknowledgement from the helper.

    def wait_ready(self):
        deadline = monotonic() + 15
        while not self.ready.wait(0.2) and not self.is_set() and monotonic() < deadline:
            pass
        if self.error:
            raise ToneError(self.error)
        if self.is_set():
            return False
        if not self.ready.is_set():
            raise ToneError(self.startup_error)
        return True

    def navigate(self, url, open_page=None):
        """Open the catalog at url; open_page: a tone page to go to once the catalog has loaded."""
        callback = parse_qs(urlsplit(url).query)['redirect_uri'][0]
        return self.send('navigate', url=url, callback=callback, **({'open': open_page} if open_page else {}))

    def next_action(self):
        while not self.is_set():
            try:
                action = self.actions.get(timeout=0.25)
                if self.error:
                    raise ToneError(self.error)
                return action
            except Empty:
                pass
        if self.error:
            raise ToneError(self.error)
        return None

    def close(self):
        deadline = monotonic() + self.close_timeout + self.terminate_timeout + self.kill_timeout + 3 * self.join_timeout
        with self._io_lock:
            if not self._closing:
                self._closing = True
                self._close_deadline = monotonic() + self.close_timeout
                if not self.is_set():
                    try:
                        self._commands.put_nowait((json.dumps({'type': 'close'}) + '\n', True))
                    except Full:
                        self._fail_locked('TONE3000 browser command queue filled during shutdown. '
                                          'Pending commands were cancelled; check HX Edit before retrying.')
        self._io_wake.set()
        if not self._shutdown_done.wait(max(0, deadline - monotonic())):
            self._fail('TONE3000 browser shutdown could not be confirmed. Close its window before retrying.')
        for thread in (self.writer, self.reader, self.monitor):
            if thread is not current_thread():
                thread.join(timeout=min(self.join_timeout, max(0, deadline - monotonic())))
