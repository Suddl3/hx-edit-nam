"""TONE3000 Select and authenticated downloads for the Windows launcher."""

import base64
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from nam_convert import IR_NAME_LIMIT, convert_batch


ORIGIN = 'https://www.tone3000.com'
API = ORIGIN + '/api/v1'
CALLBACK = '/callback'
MAX_MODEL_BYTES = 4 * 1024 * 1024
API_HOSTS = {'tone3000.com', 'www.tone3000.com'}
WINDOWS_DEVICE = re.compile(r'CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]|CONIN\$|CONOUT\$', re.IGNORECASE)


class ToneError(Exception):
    pass


# TONE3000 model ID -> the tone page it came from, so opening TONE3000 on a block whose profile was loaded
# from there goes back to that tone. Profile names end with the model ID (prepare_model).
LINKS = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'HX Edit NAM' / 'tone-links.json'


def tone_url(tone):
    """The tone's https page on TONE3000, or its standard address."""
    url = tone.get('url') or ''
    location = urlsplit(url)
    return url if location.scheme == 'https' and location.netloc in API_HOSTS else f'{ORIGIN}/tones/{int(tone["id"])}'


def remember_link(model_id, url):
    try:
        links = json.loads(LINKS.read_text(encoding='utf-8')) if LINKS.is_file() else {}
        if links.get(str(int(model_id))) == url:
            return
        links[str(int(model_id))] = url
        LINKS.parent.mkdir(parents=True, exist_ok=True)
        LINKS.with_suffix('.tmp').write_text(json.dumps(links), encoding='utf-8')
        LINKS.with_suffix('.tmp').replace(LINKS)
    except (OSError, ValueError) as error:          # the link is a convenience: auditions go on without it
        print(f'Could not remember the TONE3000 link: {error}')


def tone_link(profile):
    """The remembered tone page for a profile name ending with a TONE3000 model ID, or None."""
    match = re.search(r' (\d+)$', profile or '')
    try:
        links = json.loads(LINKS.read_text(encoding='utf-8')) if match and LINKS.is_file() else {}
    except (OSError, ValueError):
        return None
    url = links.get(match[1]) if match else None
    return url if isinstance(url, str) and urlsplit(url).scheme == 'https' and urlsplit(url).netloc in API_HOSTS else None


class NoAuthRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        if urlsplit(url).scheme != 'https':
            raise ToneError('TONE3000 returned an insecure redirect.')
        redirect = super().redirect_request(request, fp, code, message, headers, url)
        if redirect is not None:
            redirect.remove_header('Authorization')
        return redirect


def read_url(url, *, token=None, form=None, limit=MAX_MODEL_BYTES):
    headers = {'User-Agent': 'HX Edit NAM (TONE3000 Select)'}
    if token:
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.netloc not in API_HOSTS or not parts.path.startswith('/api/v1/'):
            raise ToneError('TONE3000 returned an unexpected model URL.')
        headers['Authorization'] = f'Bearer {token}'
    request = Request(url, data=urlencode(form).encode() if form else None, headers=headers)
    try:
        with build_opener(NoAuthRedirect()).open(request, timeout=15) as response:
            data = response.read(limit + 1)
    except HTTPError as error:
        if error.code in {401, 403}:
            raise ToneError('TONE3000 denied access. Reopen the catalog and sign in again.') from None
        if error.code == 429:
            raise ToneError('TONE3000 is rate limiting requests. Try again in a moment.') from None
        raise ToneError(f'TONE3000 returned HTTP {error.code}.') from None
    except (URLError, OSError) as error:
        raise ToneError('Could not reach TONE3000. Check your connection and try again.') from error
    if len(data) > limit:
        raise ToneError('The TONE3000 response exceeded the download limit.')
    return data


def callback_result(path, state):
    parts = urlsplit(path)
    query = parse_qs(parts.query, keep_blank_values=True)
    if parts.path != CALLBACK or not secrets.compare_digest(
            query.get('state', [''])[0].encode(), state.encode()):
        return False, None
    if any(len(values) != 1 for values in query.values()):
        return False, None
    if query.get('canceled') == ['true'] or query.get('error') == ['access_denied']:
        return True, None
    if 'error' in query:
        return True, ToneError('TONE3000 could not complete sign-in.')
    code = query.get('code', [''])[0]
    tone_id = query.get('tone_id', [''])[0]
    if not code or len(code) > 2048 or not re.fullmatch(r'[1-9][0-9]{0,17}', tone_id):
        return False, None
    return True, (code, int(tone_id))


def select_tone(client_id, stopped, *, open_browser):
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    state = secrets.token_urlsafe(32)
    done, result = False, None

    class Callback(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(1)

        def do_GET(self):
            nonlocal done, result
            if self.headers.get('Host') == f'127.0.0.1:{server.server_port}' and not done:
                done, result = callback_result(self.path, state)
            body = (b'Loading the selected tone in HX Edit NAM...' if done
                    else b'Invalid or expired HX Edit NAM request.')
            self.send_response(200 if done else 400)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass  # Callback URLs contain the OAuth code.

    with HTTPServer(('127.0.0.1', 0), Callback) as server:
        server.timeout = 0.25
        redirect = f'http://127.0.0.1:{server.server_port}{CALLBACK}'
        params = dict(client_id=client_id, redirect_uri=redirect, response_type='code',
                      code_challenge=challenge, code_challenge_method='S256', state=state,
                      prompt='select_tone', gears='amp_amp-cab_pedal', format='nam', architecture='2',
                      menubar='true')
        if not open_browser(f'{API}/oauth/authorize?{urlencode(params)}'):
            if stopped.is_set():
                return None
            raise ToneError('Could not open the TONE3000 catalog in your browser.')
        # The open catalog keeps this callback URL until it returns a selection.
        while not done and not stopped.is_set():
            server.handle_request()
    if stopped.is_set() or result is None:
        return None
    if isinstance(result, ToneError):
        raise result
    code, tone_id = result
    form = dict(grant_type='authorization_code', code=code, code_verifier=verifier,
                redirect_uri=redirect, client_id=client_id)
    token = json.loads(read_url(f'{API}/oauth/token', form=form))['access_token']
    return tone_id, token


def get_tone(tone_id, token):
    tone = json.loads(read_url(f'{API}/tones/{tone_id}?architecture=2', token=token))
    if tone['format'] != 'nam' or tone['gear'] not in {'amp', 'amp-cab', 'pedal'}:
        raise ToneError('Choose a NAM amp, amp + cab or pedal tone.')
    models = []
    for page in range(1, 11):
        query = urlencode(dict(tone_id=tone_id, architecture=2, page=page, page_size=100))
        batch = json.loads(read_url(f'{API}/models?{query}', token=token))
        models.extend(model for model in batch['data'] if model['architecture_version'] in (2, '2'))
        if page >= batch['total_pages']:
            break
    if not models:
        raise ToneError('This tone has no A2 profiles.')
    return tone, models


def train_capture(capture, mode, workspace, converter):
    """nam_modes.train on one capture (its progress dialog); the trained file, or None when cancelled."""
    import nam_modes
    out = nam_modes.train([capture], mode, workspace, Path(converter).with_name('nam_refine.py'))
    return out[0] if out else None


def mode_tag(mode):
    """What a converted profile's name says about its mode: ' PED' for a pedal model, ' L16' / ' L14' for a
    lighter copy, nothing as it is."""
    if mode in (None, 'original'):
        return ''
    return ' PED' if mode == 'pedal' else ' L' + mode.split('-')[0]


def prepare_model(model, token, workspace, converter, mode='original', train=train_capture):
    """Download a TONE3000 model and convert it for a User IR as `mode` (nam_modes.MODES; 'original': as it
    is). The IR name keeps the TONE3000 ID at its end, the mode's tag before it."""
    tag = mode_tag(mode)
    with tempfile.TemporaryDirectory(prefix='tone-download-', dir=workspace) as folder:
        # HX Edit can rewrite non-ASCII paths in the native import batch.
        ascii_name = model['name'].encode('ascii', 'ignore').decode('ascii')
        label = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', ascii_name).strip(' .')
        # A dot after a DOS device name still makes the filename reserved on Windows.
        if '.' in label and WINDOWS_DEVICE.fullmatch(label.split('.', 1)[0].rstrip()):
            label = '_' + label
        number = str(int(model['id']))
        if len(number) > IR_NAME_LIMIT - len('T3K ') or number == '0' or number.startswith('-'):
            raise ToneError('TONE3000 returned an invalid profile ID.')
        label = label[:IR_NAME_LIMIT - len(number) - 1 - len(tag)].rstrip(' .')
        name = f'{label}{tag} {number}' if label else f'T3K{tag} {number}'
        capture = Path(folder, name + '.nam')
        capture.write_bytes(read_url(model['model_url'], token=token))
        source = capture
        if tag:
            trained = train(capture, mode, workspace, converter)
            if trained is None:
                raise ToneError('Conversion cancelled.')
            # the IR name: the trained file renamed to the profile's name (its own folder: no clash)
            final = Path(tempfile.mkdtemp(prefix='tone-mode-', dir=workspace)) / (name + Path(trained).suffix)
            source = Path(trained).replace(final)
        converted = convert_batch([source], workspace, converter)[0]
        # Keep the original for Save to PC and Save to PC + Floor during this session, under the converted
        # profile's own name: convert_batch renames brackets (import_name), and the preview finds this
        # file again from that converted name.
        kept = Path(workspace, 'captures')
        kept.mkdir(exist_ok=True)
        capture.replace(kept / (Path(converted).stem + '.nam'))
        return converted
