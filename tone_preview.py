"""Run the catalog off the HX Edit GUI thread and show the active audition."""

from html import escape
import os
from pathlib import Path
from queue import Empty, Queue
from string import Template
from threading import Thread
from time import monotonic
from urllib.parse import urlsplit

from tone3000 import API_HOSTS, ORIGIN, ToneError, get_tone, prepare_model, select_tone
from tone_browser import ToneBrowser


CARD = Path(__file__).with_name('tone_card.html')


def render_card(tone=None, models=()):
    visible = tone is not None
    tone = tone or {}
    cover = (tone.get('images') or [''])[0]
    image = f'<img src="{escape(cover)}" alt="">' if urlsplit(cover).scheme == 'https' else ''
    return Template(CARD.read_text(encoding='utf-8')).substitute(
        hidden='' if visible else 'hidden',
        title=escape(tone.get('title', '')), author=escape('@' + tone.get('user', {}).get('username', '')),
        gear=escape(tone.get('gear', '')), license=escape(tone.get('license', '')), cover=image,
        options=''.join(f'<option value="{int(model["id"])}">{escape(model["name"])}</option>'
                        for model in models),
    )


class TonePreview:
    def __init__(self, script, workspace, converter, detached, message):
        self.script = script
        self.workspace = workspace
        self.converter = converter
        self.detached = detached
        self.message = message
        self.replies = Queue()
        self.thread = None
        self.browser = None
        self.bounds = None
        self.session = None
        self.sequence = 0

    def receive(self, payload):
        if payload['type'] == 'toneReply':
            self.replies.put(payload)
        elif payload['type'] == 'toneBounds':
            if payload['session'] == self.session:
                self.bounds = payload['bounds']
                if self.browser:
                    self.browser.send('bounds', **self.bounds)
        elif not self.thread or not self.thread.is_alive():
            self.session = payload['session']
            self.bounds = payload['bounds']
            self.thread = Thread(target=self.run, args=(self.session, payload['window']), daemon=True)
            self.thread.start()
        else:
            self.script.post({'type': 'toneCommand', 'payload': {
                'session': payload['session'], 'action': 'close', 'id': 0}})

    def command(self, session, action, **data):
        if self.detached.is_set():
            raise ToneError('HX Edit closed during the TONE3000 audition.')
        self.sequence += 1
        sequence = self.sequence
        self.script.post({'type': 'toneCommand', 'payload': {
            'id': sequence, 'session': session, 'action': action, **data}})
        deadline = monotonic() + 35
        while monotonic() < deadline and not self.detached.is_set():
            try:
                reply = self.replies.get(timeout=0.25)
            except Empty:
                continue
            if reply['id'] == sequence:
                if reply.get('error') and action == 'close':
                    raise ToneError(reply['error'])
                return reply
        raise ToneError('HX Edit did not confirm the operation. Check the IRs tab before retrying.')

    def run(self, session, window):
        browser = None
        notice = None
        slot = None
        try:
            client_id = os.environ.get('T3K_PUBLISHABLE_KEY', '').strip()
            if not client_id.startswith('t3k_pub_'):
                raise ToneError('Set T3K_PUBLISHABLE_KEY to the publishable key from TONE3000 → Settings → API Keys and restart HX Edit NAM.')
            browser = self.browser = ToneBrowser(window, self.bounds, self.detached)
            if browser.wait_ready():
                browser.send('card', html=render_card(), has_tone=False)
                slot = self.browse(browser, session, client_id)
        except (ToneError, ValueError, OSError) as error:
            if not self.detached.is_set():
                notice = (str(error), 0x10)
        except Exception:
            if not self.detached.is_set():
                notice = ('Could not complete the TONE3000 audition. Check the IRs tab before retrying.', 0x10)
        finally:
            if browser:
                browser.close()
                self.browser = None
            if not self.detached.is_set():
                try:
                    reply = self.command(session, 'close')
                    if reply.get('notice'):
                        notice = (reply['notice'], 0x40)
                except (ToneError, OSError) as error:
                    notice = notice or (str(error), 0x10)
            elif slot is not None:
                notice = (f'Check User IR {slot}. The audition profile may still be on Helix.', 0x40)
            if notice:
                self.message(*notice)

    def browse(self, browser, session, client_id):
        current = None
        slot = None
        selected = 0
        worker = None
        free_slots = []
        trial = 0

        def select():
            try:
                result = select_tone(client_id, browser, open_browser=browser.navigate)
                browser.actions.put((result, None))
            except (ToneError, ValueError, OSError, KeyError) as error:
                browser.actions.put((None, str(error)))

        def start_selection():
            nonlocal worker
            worker = Thread(target=select, daemon=True)
            worker.start()

        def status(text, busy=False):
            browser.send('status', text=text, busy=busy, trial=trial if slot is not None else None,
                         free_slots=free_slots, model=selected)

        def audition(candidate, index):
            nonlocal current, slot, selected, free_slots, trial
            tone, models, token = candidate
            status('Downloading and loading the selected profile…', busy=True)
            try:
                path = prepare_model(models[index], token, self.workspace, self.converter)
                if browser.is_set():
                    return
                slot = None
                reply = self.command(session, 'audition', file=path)
                slot = reply.get('slot')
                free_slots = reply.get('freeSlots', [])
                if reply.get('error'):
                    raise ToneError(reply['error'])
                trial += 1
                text = f'Auditioning in User IR {slot}.'
                if not free_slots:
                    text += ' No other free slot to save to.'
            except (ValueError, ToneError, OSError) as error:
                text = str(error)
                if slot is not None:
                    status(text)
                    return
            if current is not candidate:
                browser.send('card', html=render_card(tone, models), has_tone=True)
                current = candidate
            selected = index
            status(text)

        start_selection()
        while (action := browser.next_action()) is not None:
            if isinstance(action, tuple):
                selection, error = action
                worker = None
                if error:
                    browser.send('browseError', text=error)
                    continue
                if selection is None:
                    break
                else:
                    start_selection()
                    tone_id, token = selection
                    try:
                        tone, models = get_tone(tone_id, token)
                        audition((tone, models, token), 0)
                    except (ToneError, ValueError, OSError) as error:
                        status(str(error))
                    continue
            if action == 'cancel':
                break
            if action == 'finish':
                break
            if action.startswith('save:'):
                if slot is None:
                    continue
                prefix, _, destination = action.rpartition(':')
                if prefix != f'save:{trial}' or not any(destination == str(free) for free in free_slots):
                    continue
                status(f'Saving to User IR {destination}…', busy=True)
                try:
                    reply = self.command(session, 'save', slot=int(destination))
                    slot = reply.get('slot')
                    free_slots = reply.get('freeSlots', [])
                    text = f'Saved in User IR {reply["savedSlot"]}.' if reply.get('savedSlot') else ''
                    if reply.get('error'):
                        text = f'{text} {reply["error"]}'.strip()
                    status(text)
                except ToneError as error:
                    status(str(error))
                continue
            if action == 'retry' and worker is None:
                start_selection()
            if current is None:
                continue
            tone, models, _ = current
            if action == 'source':
                original = tone.get('url') or ''
                location = urlsplit(original)
                if location.scheme != 'https' or location.netloc not in API_HOSTS:
                    original = f'{ORIGIN}/tones/{int(tone["id"])}'
                browser.send('source', url=original)
            elif action.startswith('model:'):
                for index, model in enumerate(models):
                    if action[6:] == str(model['id']):
                        audition(current, index)
                        break
        return slot
