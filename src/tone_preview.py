"""Run the catalog off the HX Edit GUI thread and show the active audition."""

import base64
from html import escape
import json
import os
import re
from pathlib import Path
from queue import Empty, Full, Queue
from string import Template
from threading import Event, Lock, RLock, Thread, current_thread
from time import monotonic, sleep
from urllib.parse import urlsplit

import tempfile
import traceback

from nam_convert import IR_NAME_LIMIT, convert_batch
from nam_library import render_library
import nam_light
from tone3000 import (API_HOSTS, ORIGIN, ToneError, get_tone, prepare_model, remember_link, select_tone, tone_link,
                      tone_url)
from tone_browser import ToneBrowser


CARD = Path(__file__).with_name('tone_card.html')
KEY_HELP = ('Set T3K_PUBLISHABLE_KEY to the publishable key from TONE3000 → Settings → API Keys '
            'and restart HX Edit Unbound.')


class _LibraryActionSession:
    """Run a sidebar action through the existing workflow without a WebView.

    This is an event queue, not a second device client. TonePreview's same
    target-version, export serialization and native completion guards apply.
    """
    error = None
    close_reason = None

    def __init__(self, action, detached, status_callback=None):
        self.actions = Queue()
        self.actions.put(action)
        self.actions.put('finish')
        self.detached = detached
        self.status_callback = status_callback

    def wait_ready(self):
        return True

    def send(self, kind, **data):
        if kind == 'status' and self.status_callback:
            self.status_callback(data)

    def next_action(self):
        return None if self.detached.is_set() else self.actions.get(timeout=1)

    def is_set(self):
        return self.detached.is_set()

    def close(self):
        pass


def render_card(tone=None, models=(), library=False):
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
        library='' if library else 'hidden',
    )


class TonePreview:
    def __init__(self, script, workspace, converter, detached, message, *, browser_factory=ToneBrowser, client_id='',
                 library=None, payload_callback=None, trace_callback=None, optimize=False, pedal_block=False,
                 level=False, level_di=None, level_files=None, listen_folder=None):
        self.script = script
        # Level presets (HX Unbound): the player's recorded playing (or NAM's input file, when found)
        # played through each snapshot to bring every preset to one output standard (nam_level_flow).
        # level_di: one more place a build says NAM's input file is.
        self.level = bool(level)
        self.level_di = Path(level_di) if level_di else None
        self.level_files = level_files or {}     # restore_file / records_file (tests use their own)
        self.level_flow = None
        self.optimize = bool(optimize)   # Optimize (versions of a capture): experiment builds only for now
        # The Floor's firmware has the NAM Pedal block (builds for it: res/nam-pedal.flag); until a
        # firmware marker can be read, the build says so.
        self.pedal_block = bool(pedal_block)
        # Optimize A/B: where the Listen renders are kept (nam_ab.RENDERS; tests use their own).
        self.listen_folder = Path(listen_folder) if listen_folder else None
        self.library = library  # nam_library.Library on Windows; None hides the library
        self.payload_callback = payload_callback
        self.workspace = workspace
        self.converter = converter
        self.detached = detached
        self.message = message
        self.browser_factory = browser_factory
        self.client_id = client_id
        self.replies = Queue()
        self.command_lock = RLock()
        self.thread = None
        self.lifecycle_lock = Lock()
        self._library_notification_lock = Lock()
        self._library_notification_pending = None
        self.browser = None
        self.bounds = None
        self.session = None
        self.tile = None
        self.profile = ''
        self.closing = False
        self.queued = None
        self.sequence = 0
        self.navigation = False
        self.navigation_state = {}
        self.initial_library = False
        self.initial_level = False     # opened from the Level button: the Level page, not the library
        self.initial_look = False      # opened from HX Unbound > Floor look: that page, not the library
        self.initial_action = None
        self.headless = False
        self.close_reason = None
        self.close_intent = Event()
        self.sidebar_status = None
        self.drop_handler = None       # (identity, x, y): the NAM tab's drop onto the chain (nam_sidebar)
        self.native_drag_handler = None   # (identity): a drag of a capture on the Floor, run by HX Edit (nam_sidebar)
        self.target_ready = Event()
        # The trace sink may do disk I/O. Never run it on a Frida callback or the
        # audition worker, and bound memory even when the sink stops responding.
        self._trace_queue = Queue(maxsize=128) if trace_callback else None
        self._trace_stop = Event()
        self._trace_thread = None
        if trace_callback:
            self._trace_thread = Thread(target=self._write_diagnostics, args=(trace_callback,), daemon=True)
            self._trace_thread.start()
        self._library_unsubscribe = library.subscribe(self.library_changed) if library else None

    def _write_diagnostics(self, callback):
        while not self._trace_stop.is_set() or not self._trace_queue.empty():
            try:
                event = self._trace_queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                callback(event)
            except Exception:
                pass  # Diagnostics must not change the audition's outcome.

    def close_diagnostics(self):
        """Launcher shutdown only: drain briefly, with a daemon fallback for a stuck sink."""
        self._trace_stop.set()
        if self._trace_thread and self._trace_thread is not current_thread():
            self._trace_thread.join(timeout=0.25)
        if self._library_unsubscribe:
            self._library_unsubscribe()
            self._library_unsubscribe = None

    def _trace(self, phase, stage, session, *, request=None, action=None, started=None):
        if self._trace_queue is None or self._trace_stop.is_set():
            return
        # Strict values, not just safe field names: never serialize a payload,
        # exception, catalog identity, local path, URL, or token into this trace.
        if phase not in {'browser', 'selection', 'metadata', 'prepare', 'native-command'} or stage not in {
                'started', 'ready', 'unavailable', 'received', 'completed', 'failed',
                'queued', 'replied', 'reply-error', 'timeout', 'detached', 'helper-failed', 'closing', 'closed'}:
            return
        event = {'type': 'tonePreviewPhase', 'phase': phase, 'stage': stage}
        if type(session) is int:
            event['session'] = session
        if type(request) is int:
            event['request'] = request
        if action in {'audition', 'save', 'load', 'use', 'bind', 'end', 'close'}:
            event['action'] = action
        if started is not None:
            event['elapsedMs'] = max(0, round((monotonic() - started) * 1000))
        try:
            self._trace_queue.put_nowait(event)
        except Full:
            pass

    def _trace_call(self, phase, session, operation):
        started = monotonic()
        self._trace(phase, 'started', session)
        try:
            result = operation()
        except Exception:
            self._trace(phase, 'failed', session, started=started)
            raise
        self._trace(phase, 'completed', session, started=started)
        return result

    def inventory_changed(self):
        """Notify the browser after the shared library accepts an atomic inventory."""
        if self.browser:
            self.browser.actions.put({'inventory': True})

    def library_changed(self):
        """Shared sidebar edits also update an already-open expanded view."""
        with self._library_notification_lock:
            browser = self.browser
            if browser and self._library_notification_pending is not browser:
                self._library_notification_pending = browser
                browser.actions.put({'libraryChanged': True})

    def close_requested(self):
        # Browser's IPC reader records Escape/X immediately, while this worker
        # may still be downloading/converting. Do not wait for its queued close
        # action before cancelling a prepared edit. Native-focused Escape sets
        # the same intent in receive(), without depending on helper focus.
        reason = getattr(self.browser, 'close_reason', None)
        return self.close_intent.is_set() or (isinstance(reason, str) and bool(reason))

    def register_payload(self, identity, path):
        if self.navigation and self.library:
            self.library.register_payload(identity, path)
            if self.payload_callback:
                self.payload_callback(self.library.payload_metadata())

    def receive(self, payload):
        if payload['type'] == 'toneReply':
            self.replies.put(payload)
        elif payload['type'] == 'toneTarget':
            if payload['session'] == self.session and self.navigation:
                self.navigation_state = dict(payload)
                self.target_ready.set()
                if self.browser:
                    self.browser.send('target', **{key: value for key, value in payload.items() if key != 'type'})
                    self.browser.actions.put({'navigation': payload})
        elif payload['type'] == 'toneFocus':
            if payload['session'] == self.session and self.browser:
                self.browser.send('focus')
                if payload.get('page') in ('library', 'catalog', 'level', 'look'):
                    self.browser.actions.put(payload['page'])
            else:
                self._abandon_if_gone(payload['session'])
        elif payload['type'] == 'toneAction':
            if payload['session'] == self.session and self.browser:
                self.browser.actions.put(payload['action'])
            else:
                self._abandon_if_gone(payload['session'])
        elif payload['type'] == 'toneBounds':
            if payload['session'] == self.session:
                self.bounds = payload['bounds']
                if self.browser:
                    self.browser.send('bounds', **self.bounds)
        elif payload['type'] == 'toneDismiss':
            # HX Edit picked another model or tile under the pane: close it like Close does
            if payload['session'] == self.session and self.browser:
                self.close_intent.set()
                self.browser.actions.put({'close': payload.get('reason', 'selection-changed')})
        else:
            with self.lifecycle_lock:
                if self.closing and self.thread and self.thread.is_alive():
                    self.queued = payload       # a tile picked as the last pane closes: opens next
                    return
                if not self.thread or not self.thread.is_alive():
                    self.closing = False
                    self.session = payload['session']
                    self.tile = payload.get('tile')  # the TONE3000 tile it was opened from: its block and modes
                    self.profile = payload.get('profile') or ''  # the block's profile name (its tone opens first)
                    self.navigation = bool(payload.get('navigation'))
                    self.navigation_state = {}
                    self.target_ready.clear()
                    self.initial_level = bool(payload.get('level'))
                    self.initial_look = bool(payload.get('look'))
                    self.initial_library = bool(payload.get('library')) or self.initial_level or self.initial_look
                    self.initial_action = payload.get('libraryAction')
                    self.headless = bool(payload.get('headless'))
                    self.close_reason = None
                    self.close_intent.clear()
                    self.bounds = payload['bounds']
                    self.thread = Thread(target=self.run, args=(self.session, payload['window']), daemon=True)
                    self.thread.start()
                    return
            self.script.post({'type': 'toneCommand', 'payload': {
                'session': payload['session'], 'action': 'close', 'id': 0}})

    def _abandon(self, session):
        """Tell HX Edit that this session has no supervisor any more. It edits nothing there: an
        unconfirmed preview stays on the device for review."""
        if self.detached.is_set():
            return
        self._trace('browser', 'abandoned', session)
        try:
            self.script.post({'type': 'toneAbandon', 'payload': {'session': session}})
        except Exception:
            pass

    def _abandon_if_gone(self, session):
        """HX Edit addressed a session that is not running here (its helper or worker ended without a
        confirmed close). Release it there, so the next Browse click starts a new one."""
        with self.lifecycle_lock:
            running = self.thread is not None and self.thread.is_alive()
            current = running and session == self.session
        if not current:
            self._abandon(session)

    def command(self, session, action, **data):
        with self.command_lock:
            return self._command(session, action, **data)

    def _command(self, session, action, **data):
        started = monotonic()

        def check_helper(request=None):
            # Closing an already-exited helper still owes native preview cleanup.
            # Its process health is irrelevant to that one idempotent request.
            if action == 'close':
                return
            error = getattr(self.browser, 'error', None)
            if isinstance(error, str) and error:
                self._trace('native-command', 'helper-failed', session, request=request,
                            action=action, started=started)
                raise ToneError(error + '\n\nThis operation is unconfirmed; no command was retried.')

        if self.detached.is_set():
            self._trace('native-command', 'detached', session, action=action, started=started)
            raise ToneError('HX Edit closed during the TONE3000 audition.')
        check_helper()
        if action not in ('close', 'end') and self.close_requested():
            raise ToneError('Preview cancelled before the pending edit was sent.')
        self.sequence += 1
        sequence = self.sequence
        self._trace('native-command', 'queued', session, request=sequence, action=action)
        try:
            self.script.post({'type': 'toneCommand', 'payload': {
                'id': sequence, 'session': session, 'action': action, **data}})
        except Exception:
            self._trace('native-command', 'failed', session, request=sequence, action=action, started=started)
            raise
        deadline = monotonic() + 35
        while monotonic() < deadline and not self.detached.is_set():
            check_helper(sequence)
            try:
                reply = self.replies.get(timeout=0.25)
            except Empty:
                continue
            if reply['id'] == sequence:
                if reply.get('session', session) != session:
                    continue
                check_helper(sequence)
                self._trace('native-command', 'replied', session, request=sequence, action=action, started=started)
                if reply.get('error'):
                    self._trace('native-command', 'reply-error', session, request=sequence, action=action)
                if reply.get('error') and action == 'close':
                    raise ToneError(reply['error'])
                return reply
        self._trace('native-command', 'detached' if self.detached.is_set() else 'timeout', session,
                    request=sequence, action=action, started=started)
        raise ToneError('HX Edit did not confirm the operation. Check the IRs tab before retrying.')

    def run(self, session, window):
        browser = None
        notice = None
        slot = None
        started = monotonic()
        self._trace('browser', 'started', session)
        try:
            client_id = (self.client_id or os.environ.get('T3K_PUBLISHABLE_KEY', '')).strip()
            if not client_id.startswith('t3k_pub_'):
                if not self.library:
                    raise ToneError(KEY_HELP)
                client_id = ''  # the local library still works without a TONE3000 key
            browser = self.browser = (_LibraryActionSession(self.initial_action, self.detached, self.sidebar_status)
                                      if self.headless else self.browser_factory(window, self.bounds, self.detached))
            # Native HX Edit grants foreground permission only on an explicit
            # Browse click. This carries identity; it never focuses a window.
            browser_pid = getattr(getattr(browser, 'process', None), 'pid', None)
            if self.navigation and isinstance(browser_pid, int) and browser_pid > 0:
                self.script.post({'type': 'toneBrowserProcess', 'payload': {'session': session, 'pid': browser_pid}})
            if browser.wait_ready():
                self._trace('browser', 'ready', session, started=started)
                if not (self.library and (self.initial_library or not client_id)):  # browse() sends the library card
                    browser.send('card', html=render_card(library=bool(self.library)), has_tone=False)
                if self.navigation:
                    if self.headless and not self.target_ready.wait(5):
                        raise ToneError('HX Edit did not report the selected NAM block. Nothing was loaded.')
                    browser.send('target', **{key: value for key, value in self.navigation_state.items() if key != 'type'})
                try:
                    slot = self.browse(browser, session, '' if self.headless else client_id)
                finally:
                    flow, self.level_flow = self.level_flow, None
                    if flow is not None:
                        flow.close()   # a scan or Apply stops, the routing goes back, the watcher ends
            else:
                self._trace('browser', 'unavailable', session, started=started)
        except (ToneError, ValueError, OSError) as error:
            self._trace('browser', 'failed', session, started=started)
            traceback.print_exc()            # the local launch log only; the trace never holds exceptions
            if self.headless and self.sidebar_status:
                self.sidebar_status({'text':str(error), 'error':str(error), 'busy':False})
            if not self.detached.is_set():
                notice = (str(error), 0x10)
        except Exception:
            self._trace('browser', 'failed', session, started=started)
            traceback.print_exc()            # the local launch log only
            if self.headless and self.sidebar_status:
                self.sidebar_status({'text':'Could not complete the library action. Nothing was retried.', 'busy':False})
            if not self.detached.is_set():
                notice = ('Could not complete the TONE3000 audition. Check the IRs tab before retrying.', 0x10)
        finally:
            helper_error = getattr(browser, 'error', None)
            if isinstance(helper_error, str) and helper_error:
                notice = notice or (helper_error + '\n\nThe operation is unconfirmed; check HX Edit before another audition.', 0x10)
            # Preserve the native binding until restoration is confirmed. Closing
            # a docked helper first can destroy its parameter view/controller.
            # Native restoration addresses the original block, never the visible
            # caption, and remains required if the helper has already exited.
            if not self.detached.is_set():
                self.closing = True
                try:
                    reason = self.close_reason or getattr(browser, 'close_reason', None)
                    if not isinstance(reason, str):
                        reason = 'helper-disconnected'
                    reply = self.command(session, 'close', reason=reason)
                    if reply.get('notice'):
                        notice = notice or (reply['notice'], 0x40)
                except (ToneError, OSError) as error:
                    notice = notice or (str(error), 0x10)
                    # No confirmed close: HX Edit may still hold this session, and would then ignore
                    # Browse TONE3000 until it restarts. Tell it this side has gone.
                    self._abandon(session)
            if browser:
                self._trace('browser', 'closing', session, started=started)
                browser.close()
                self.browser = None
                self._trace('browser', 'closed', session, started=started)
                if self.sidebar_status and not self.headless:
                    # A cancelled download sends no final status of its own; do not leave the NAM
                    # sidebar showing "Downloading…" with Load/Use disabled.
                    self.sidebar_status({'text': '', 'busy': False})
                error = getattr(browser, 'error', None)
                if isinstance(error, str) and error:
                    notice = notice or (error + '\n\nThe operation is unconfirmed; check HX Edit before another audition.', 0x10)
            if self.detached.is_set() and slot is not None:
                notice = notice or (f'Check User IR {slot}. The audition profile may still be on Helix.', 0x40)
            # Device cleanup and close acknowledgement have finished. A modal
            # notice can remain open indefinitely; it must not reject new starts.
            # An old notice thread must never clear a newer session's ownership.
            with self.lifecycle_lock:
                queued = None
                if self.thread is current_thread():
                    self.thread = None
                    self.closing = False
                    queued, self.queued = self.queued, None
            if queued:
                self.receive(queued)
            if notice:
                self.message(*notice)

    def browse(self, browser, session, client_id):
        current = None
        slot = None
        selected = 0
        worker = None
        free_slots = []
        trial = 0
        kept = None  # the current trial's capture for Add to library / Save to Helix
        text = ''
        library = False
        library_content = None
        light = None  # the lighter-capture training shown on the library page
        light_job = None
        optimizing = None    # Optimize: the version being made for one capture, shown on the library page
        optimize_job = None
        listening = None     # Optimize A/B on this PC (nam_ab): {id, key, which, running, match, source, error}
        listen_job = None
        player = None        # nam_ab.Player, made at the first Listen
        leveling = None      # Level presets: a nam_level_flow.LevelFlow while the Level page has been opened
        level_page = False   # the page shows Level instead of the library
        look_page = False    # the page shows the Floor look instead of the library
        look_saved = None    # {path, at, slots}: the Floor look file written in this session
        tone_modes = {}  # TONE3000 tone ID -> the conversion chosen for it (nam_modes.MODES key)
        mode_now = None  # the conversion of the profile being auditioned
        taken = None     # every User IR slot taken: [slot, name] pairs to choose one to replace from
        retry = None     # the audition that found no free slot, run again with the chosen slot
        loaded_now = False  # the profile shown was just loaded on the Floor: it needs no Preview button

        reopen = tone_link(self.profile)  # the block's profile came from this tone: its page opens first

        def select():
            nonlocal reopen
            page, reopen = reopen, None
            try:
                result = self._trace_call('selection', session, lambda: select_tone(
                    client_id, browser, open_browser=lambda url: browser.navigate(url, page)))
                browser.actions.put((result, None))
            except (ToneError, ValueError, OSError, KeyError) as error:
                browser.actions.put((None, str(error)))

        def start_selection():
            nonlocal worker
            worker = Thread(target=select, daemon=True)
            worker.start()

        def target():
            """What the pane loads into: the tile's block, and the conversion of the current audition."""
            if self.navigation:
                return self.navigation_state.get('label') or 'No audition target selected'
            import nam_modes
            block = (self.tile or {}).get('block', 'NAM A2 Lite')
            title = next((t for k, *_, t, _ in nam_modes.MODES if k == mode_now), None)
            return f'{block} · {title.rpartition(" · ")[0]}' if title else block

        listen_source_seen = [0.0, None]

        def listen_source_name():
            """The guitar "Hear on this PC" would play ('' when there is none), looked up at most every 2 s."""
            import time
            if time.monotonic() - listen_source_seen[0] > 2.0:
                import nam_ab
                try:
                    source = nam_ab.pick_source(extra=[self.level_di] if self.level_di else [])
                except Exception:
                    source = None
                listen_source_seen[:] = [time.monotonic(), source['name'] if source else '']
            return listen_source_seen[1]

        def status(message, busy=False):
            nonlocal text
            if not busy:
                text = message
            if not self.headless and self.sidebar_status:
                self.sidebar_status({'text':message, 'busy':busy})
            inventory = None
            if self.navigation and self.library:
                inventory = {'rows': [dict(id=identity, irSlots=entry.get('irSlots', []), pendingSlots=entry.get('pendingSlots', []),
                                           versionSlots=entry.get('versionSlots', {}),
                                           slotState=entry.get('slotState', 'unverified'))
                                      for identity, entry in self.library.entries().items()]}
            browser.send('status', text=message, busy=busy, trial=trial if slot is not None else None,
                         free_slots=free_slots, model=selected, keep=bool(self.library) and kept is not None and 'id' not in kept,
                         light=light, target=target(), replace=taken or [],
                         navigation=self.navigation, inventory=inventory,
                         libraryView=self.library.view_state() if self.library else None,
                         can_audition=not self.navigation or bool(self.navigation_state.get('bound')),
                         installed=loaded_now,
                         can_train=not self.navigation,
                         optimize=optimizing, can_optimize=self.optimize, pedal_block=self.pedal_block,
                         listen=listening, listen_source=listen_source_name() if self.optimize else None,
                         block_model=self.navigation_state.get('blockModel'),
                         block_slot=self.navigation_state.get('blockSlot'),
                         level=leveling.view() if leveling else None, can_level=self.level,
                         levelPage=level_page, lookPage=look_page,
                         look=look_view() if look_page else None,
                         can_install=self.navigation and bool(self.navigation_state.get('bound')) and
                         not self.navigation_state.get('preview') and not self.navigation_state.get('quarantined'))

        def show_card():
            nonlocal library_content
            if library:
                entries = self.library.entries()
                library_content = library_signature(entries)
                browser.send('card', html=render_library(entries, self.library.view_state(), self.library.pictures),
                             has_tone=False, library=True)
            elif current:
                browser.send('card', html=render_card(*current[:2], library=bool(self.library)), has_tone=True)
            else:
                browser.send('card', html=render_card(library=bool(self.library)), has_tone=False)
            status(text)

        def library_signature(entries):
            # Slot proof and view selection update in place. Rebuild only when
            # rows or persistent metadata change, preserving search input focus
            # through scans and ordinary sidebar selection/filter changes.
            transient = {'irSlots', 'pendingSlots', 'slotState'}
            return json.dumps({identity:{key:value for key,value in entry.items() if key not in transient}
                               for identity,entry in entries.items()}, sort_keys=True)

        def library_visibility():
            if self.navigation and not self.headless:
                self.script.post({'type': 'toneLibraryVisibility',
                                  'payload': {'session': session, 'visible': library}})

        def play(prepare, capture, replace=None, again=None):
            """Audition a converted profile; return the status text, or None when already reported.
            No free User IR slot: the card lists the taken ones, and again(slot) auditions into the chosen one."""
            nonlocal slot, free_slots, trial, kept, taken, retry, loaded_now
            taken = retry = None
            loaded_now = False
            try:
                target_version = self.navigation_state.get('version') if self.navigation else None
                if self.navigation and not self.navigation_state.get('bound'):
                    return 'Select a NAM block in HX Edit to audition this tone.'
                path = self._trace_call('prepare', session, prepare)
                if browser.is_set() or self.close_requested():
                    return None
                slot = None
                if self.navigation and (not self.navigation_state.get('bound') or
                                        self.navigation_state.get('version') != target_version):
                    return 'The selected block changed during download. Select the tone again to audition on the new block.'
                reply = self.command(session, 'audition', file=path,
                                     **({'targetVersion': target_version} if self.navigation else {}),
                                     **({'replace': replace} if replace else {}))
                slot = reply.get('slot')
                free_slots = reply.get('freeSlots', [])
                if reply.get('replace') and again:
                    taken, retry = reply['replace'], again
                    status(reply['error'])
                    return None
                if reply.get('error'):
                    raise ToneError(reply['error'])
                trial += 1
                kept = capture(path) if self.library else None
                message = ('Previewing on the selected block. Nothing is saved yet.' if self.navigation else
                           f'Auditioning in User IR {slot}.')
                if not free_slots:
                    message += ' No other free slot to save to.'
            except (ValueError, ToneError, OSError) as error:
                message = str(error)
                if slot is not None:
                    status(message)
                    return None
            return message

        def tone_mode(key, name):
            """How to convert a profile for the TONE3000 tile this card was opened from: the tile's only
            mode, or asked once per tone / library capture among its modes. None: cancelled."""
            import nam_modes
            nonlocal mode_now
            keys = (self.tile or {}).get('modes') or ['original']
            if key not in tone_modes:
                mode = nam_modes.choose_tile_mode(name, keys)
                if mode is None:
                    return None
                tone_modes[key] = mode
            mode_now = tone_modes[key]
            return mode_now

        def audition(candidate, index, replace=None):
            nonlocal current, selected
            if self.close_requested():
                return
            tone, models, token = candidate
            if self.navigation and not self.navigation_state.get('bound'):
                current, selected = candidate, index
                if not library:
                    browser.send('card', html=render_card(tone, models, library=bool(self.library)), has_tone=True)
                status('Tone selected for browsing. Select a NAM block in HX Edit, then audition this profile.')
                return
            mode = tone_mode(f'tone:{tone.get("id")}', tone.get('title') or 'TONE3000 profile')
            if mode is None:
                status('Cancelled: choose the conversion to audition this tone.')
                return
            status('Downloading and loading the selected profile…' if mode == 'original' else
                   'Downloading and converting the selected profile…', busy=True)

            def capture(path):
                source = tone.get('url') or f'{ORIGIN}/tones/{int(tone["id"])}'
                name = ' – '.join(part for part in (tone.get('title', ''), models[index]['name']) if part)
                return dict(path=Path(self.workspace, 'captures', Path(path).stem + '.nam'),
                            name=name, gear=tone.get('gear', ''), source=source, wav=path)

            remember_link(models[index]['id'], tone_url(tone))   # the profile's name ends with this model ID
            message = play(lambda: prepare_model(models[index], token, self.workspace, self.converter, mode), capture,
                           replace, lambda chosen: audition(candidate, index, chosen))
            if message is None:
                return
            if current is not candidate:
                current = candidate
                if not library:
                    browser.send('card', html=render_card(tone, models, library=bool(self.library)), has_tone=True)
            selected = index
            status(message)

        def audition_local(identity, replace=None):
            nonlocal current
            entry = self.library.entries().get(identity, {})
            mode = tone_mode(f'library:{identity}', entry.get('name') or 'Library capture')
            if mode is None:
                status('Cancelled: choose the conversion to audition this capture.')
                return
            status('Loading the capture from the library…', busy=True)

            def prepare():
                folder = tempfile.mkdtemp(prefix='library-', dir=self.workspace)
                capture = self.library.export(identity, folder)
                if mode != 'original':                  # the tile's block: a pedal model or a lighter copy
                    import nam_modes
                    out = nam_modes.train([capture], mode, self.workspace, Path(self.converter).with_name('nam_refine.py'))
                    if not out:
                        raise ToneError('Conversion cancelled.')
                    capture = out[0]
                path = convert_batch([capture], self.workspace, self.converter)[0]
                self.register_payload(identity, path)
                return path

            message = play(prepare, lambda _path: dict(id=identity), replace,
                           lambda chosen: audition_local(identity, chosen))
            if message is not None:
                current = None  # the TONE3000 card no longer describes the audition
                status(message)

        def install_local(identity, use_only=False, capture_version=None):
            """capture_version 'pedal': the kept NAM Pedal version instead of the original. On a NAM Pedal firmware
            the block's model follows what goes on it (a Pedal model in a NAM A2 Lite block is silent, and the
            other way round), so HX Edit changes the block's model first when it differs."""
            nonlocal current
            version = self.navigation_state.get('version')
            if not self.navigation_state.get('bound'):
                status('Select a NAM block in HX Edit to load this capture.')
                return
            if self.navigation_state.get('preview') or self.navigation_state.get('quarantined'):
                status('End the current preview before loading a library capture.')
                return
            status('Using the installed capture…' if use_only else 'Loading the capture into the lowest empty User IR slot…', busy=True)
            try:
                values = dict(identity=identity, targetVersion=version)
                if self.pedal_block:
                    values['model'] = 'HD2_AmpNAMPedal' if capture_version == 'pedal' else 'HD2_AmpNAMLite'
                if capture_version == 'pedal':
                    made = self.library.version_path(identity, 'pedal')
                    if not made.is_file():
                        raise ToneError('This capture has no Pedal version yet. Optimize it first.')
                    from floor_capture import validate_floor_capture_files
                    validate_floor_capture_files([made], pedal=True)
                    values.update(version='pedal', file=str(made))
                    if self.payload_callback is not None:      # its slot is then recognized as this capture's Pedal version
                        self.payload_callback(self.library.payload_metadata())
                elif not use_only:
                    folder = tempfile.mkdtemp(prefix='library-', dir=self.workspace)
                    capture = self.library.export(identity, folder)
                    path = convert_batch([capture], self.workspace, self.converter)[0]
                    self.register_payload(identity, path)
                    values['file'] = path
                if not self.navigation_state.get('bound') or self.navigation_state.get('version') != version:
                    status('The selected block changed. Load this capture again to use the new block.')
                    return
                result = self.command(session, 'use' if use_only else 'load', **values)
                if result.get('error'):
                    raise ToneError(result['error'])
                destination = result.get('loadedSlot') or result.get('usedSlot')
                if not isinstance(destination, int) or not 1 <= destination <= 127:
                    raise ToneError('HX Edit did not confirm the library destination.')
                current = None
                if capture_version == 'pedal':
                    status(f'The Pedal version plays in User IR {destination} on a NAM Pedal block. Preset remains unsaved.')
                elif result.get('loadedSlot'):
                    self.library.mark_pending(identity, destination)
                    status(f'Loaded into User IR {destination}; verifying capture content. Preset remains unsaved.')
                else:
                    status(f'Using the verified capture in User IR {destination}. Preset remains unsaved.')
            except (OSError, ValueError, ToneError) as error:
                status(str(error))

        def keep():
            """Keep the auditioned capture on this PC. False: its original is not available to keep."""
            if not kept:
                return False
            if 'id' not in kept:
                if not kept['path'].is_file():
                    return False
                kept['id'] = self.library.add(kept['path'], name=kept['name'], gear=kept['gear'],
                                              source=kept['source'])
                self.register_payload(kept['id'], kept['wav'])
            return True

        if client_id:
            start_selection()
        if self.library and (not client_id or self.initial_library):
            library = True
            show_card()
        library_visibility()
        def start_light(identity, layers, quality):
            nonlocal light, light_job
            entry = self.library.entries()[identity]
            if self.navigation:
                status('This Floor build uses the original A2 Lite engine. Shortened layer variants require a separately compatible firmware build.')
                return
            if not entry.get('canTrain', True):
                status('A lighter version needs the original .nam capture. This library entry contains a converted device capture.')
                return
            layers, steps = int(layers), nam_light.STEPS[quality]
            if layers not in nam_light.LAYERS:
                raise ValueError(f'Unknown layer count: {layers}')
            folder = Path(tempfile.mkdtemp(prefix='light-', dir=self.workspace))
            capture = self.library.export(identity, folder, original=True)
            suffix = f' L{layers}'
            output = folder / 'out' / (entry['irName'][:IR_NAME_LIMIT - len(suffix)].rstrip(' .') + suffix + '.nam')
            output.parent.mkdir()
            light = {'running': True, 'id': identity, 'layers': layers, 'step': 0, 'steps': steps,
                     'name': f'{entry["name"]} [{layers} layers]', 'gear': entry.get('gear', ''),
                     'source': entry.get('source', ''), 'output': str(output)}
            light_job = nam_light.LightJob(Path(self.converter).with_name('nam_refine.py'), capture, output, layers,
                                           steps, lambda state: browser.actions.put({'light': state}))
            status(f'Training a {layers}-layer version…')

        def start_optimize(identity):
            nonlocal optimizing, optimize_job
            import nam_optimize
            entry = self.library.entries()[identity]
            if not self.optimize:
                status('Optimize is not available in this build.')
                return
            if not entry.get('canTrain', True):
                status('Optimize needs the original .nam capture. This library entry is a converted device capture.')
                return
            key = nam_optimize.version_for(entry.get('gear', ''))
            if key is None:
                status('Optimize makes a NAM Pedal version, for pedal captures. Amps and cabs get a lighter '
                       'version once the Floor firmware has layer skipping.')
                return
            folder = Path(tempfile.mkdtemp(prefix='optimize-', dir=self.workspace))
            capture = self.library.export(identity, folder, original=True)
            output = folder / 'out' / 'version.wav'
            output.parent.mkdir()
            fitter = nam_optimize.fitter_path(self.converter)
            optimizing = {'running': True, 'id': identity, 'key': key, 'step': 0, 'steps': 400,
                          'output': str(output), 'converter': nam_optimize.converter_id(fitter)}
            optimize_job = nam_optimize.OptimizeJob(fitter, capture, output, 0, 400,
                                                    lambda state: browser.actions.put({'optimize': state}))
            status(f'Optimizing "{entry.get("name", "")}" (about 30 seconds)…', busy=True)

        def optimize_event(state):
            nonlocal optimizing, optimize_job
            optimizing.update(state)
            if 'error' in state:
                optimizing['running'] = False
                optimize_job = None
                status('Optimize cancelled.' if state.get('cancelled') else f'Optimize failed: {state["error"]}')
            elif state.get('done'):
                optimizing['running'] = False
                optimize_job = None
                try:
                    self.library.add_version(optimizing['id'], optimizing['key'], optimizing['output'],
                                             error=state.get('esr'), converter=optimizing['converter'])
                    import nam_library
                    quality = nam_library.version_quality(state.get('esr'))
                    message = ('Pedal version ready: it sounds the same as the original.' if quality == 'same' else
                               'Pedal version ready, but it may sound different from the original. Compare '
                               'before using it.' if quality == 'different' else 'Pedal version ready.')
                    message += (' It plays in a NAM Pedal block.' if self.pedal_block else
                                ' It plays in a NAM Pedal block, which needs Floor firmware with that block.')
                except (OSError, ValueError, KeyError) as error:
                    optimizing['error'] = str(error)
                    message = f'Could not keep the optimized version: {error}'
                if library:
                    show_card()
                status(message)
            else:
                status(text, busy=True)

        def listen_renders():
            import nam_ab
            return nam_ab.Renders(self.listen_folder or nam_ab.RENDERS)

        def listen(identity, which):
            """Listen: Original / Pedal. Plays the kept pair at once, else renders it first (nam_ab)."""
            nonlocal listening, listen_job
            import nam_ab
            import nam_optimize
            if not self.optimize:
                status('Optimize is not available in this build.')
                return
            if which not in nam_ab.WHICH:
                return
            entry = self.library.entries()[identity]
            if 'pedal' not in (entry.get('versions') or {}) or not entry.get('canTrain'):
                status('This capture has no Pedal version to compare yet. Optimize it first.')
                return
            if listen_job is not None:
                if listening and listening.get('id') == identity:
                    listening['which'] = which          # plays this one when the render is done
                    status(text, busy=True)
                return
            source = nam_ab.pick_source(extra=[self.level_di] if self.level_di else [])
            if source is None:
                status('Listen needs a guitar to play through both: record your guitar on the Level page '
                       '(HX Unbound menu), or put NAM\'s v3 input file in Downloads.')
                return
            capture = self.library.original_path(identity)
            version = self.library.version_path(identity, 'pedal')
            renders = listen_renders()
            key = nam_ab.cache_key(source, capture, version)
            listening = {'id': identity, 'key': 'pedal', 'which': which, 'running': False, 'match': None,
                         'source': source['name'], 'error': None, 'playing': None}
            match = renders.lookup(key)
            if match is not None:
                listening['match'] = match
                listen_play()
                return
            listening['running'] = True
            spec = {'fitter': str(nam_optimize.fitter_path(self.converter)), 'capture': str(capture),
                    'version': str(version), 'source': source['path'], 'start': source['start'],
                    'seconds': source['seconds'], 'peak': source['peak']}
            listen_job = nam_ab.RenderJob(renders, key, spec, lambda state: browser.actions.put({'listen': state}))
            status(f'Rendering {source["name"]} through the original and the Pedal version (about 10 seconds)…',
                   busy=True)

        def listen_play():
            nonlocal player
            import nam_ab
            which = listening['which']
            try:
                player = player or nam_ab.Player()
                player.play(listen_renders().path(listening['match']['key'], which))
                listening['playing'] = which
                status(f'Playing the {"original" if which == "original" else "Pedal version"} on this PC '
                       f'({listening["source"]}). {nam_ab.match_text(listening["match"])}')
            except (OSError, RuntimeError, ImportError) as error:
                listening['playing'] = None
                status(f'Could not play on this PC: {error}')

        def listen_event(state):
            nonlocal listen_job
            listen_job = None
            listening['running'] = False
            if 'error' in state:
                listening['error'] = None if state.get('cancelled') else state['error']
                status('Listen cancelled.' if state.get('cancelled') else f'Listen failed: {state["error"]}')
            else:
                listening['match'] = state['match']
                listen_play()

        def level_command(action, **data):
            reply = self.command(session, action, **data)
            if reply.get('error'):
                raise ToneError(reply['error'])
            return reply

        def look_slots():
            from floor_memory import FloorMemory
            return {slot: {'name': name, 'contentId': content}
                    for slot, name, content in FloorMemory(self.library.folder).slots()}

        def look_view():
            import floor_store
            from nam_catalog import load_settings
            return floor_store.view(look_slots(), self.library.payload_metadata(), load_settings().get('floorLook'),
                                    look_saved)

        def look_open():
            # The Floor look page (HX Unbound menu): each capture slot's picture and the backgrounds, saved to the
            # file the Floor's picture tool reads. Its own window, like Level.
            nonlocal look_page, level_page, library
            if not look_page:
                browser.send('chrome', targetBar=False, title='Floor look · HX Unbound')
                browser.send('presentation', mode='floating', focus=True)
            look_page, level_page = True, False
            if not library:
                library = True
                show_card()          # the page's library rows: a capture's picture is chosen on its library row
            status('Floor look: the pictures and backgrounds Focus View shows. Save writes the file for the Floor.')

        def look_assign(choice):
            import floor_store
            from nam_catalog import load_settings, save_setting
            look = dict(load_settings().get('floorLook') or {})
            if choice is None:
                look.pop('assign', None)
            else:
                look['assign'] = floor_store.assignment(choice, [scene['id'] for scene in floor_store.scenes()])
            save_setting('floorLook', look)
            status('Backgrounds changed. Save to update the file for the Floor.' if choice is not None else
                   'Backgrounds back to the defaults. Save to update the file for the Floor.')

        def look_save():
            nonlocal look_saved
            import floor_store
            from floor_memory import FloorMemory
            from nam_catalog import load_settings
            path, store = floor_store.write_for(self.library, FloorMemory(self.library.folder),
                                                load_settings().get('floorLook'))
            from datetime import datetime
            look_saved = {'path': str(path), 'at': datetime.now().strftime('%H:%M'), 'slots': len(store['nam']['slots'])}
            status(f'Saved for the Floor: {look_saved["slots"]} capture pictures and the backgrounds. It goes onto the '
                   'Floor after the firmware install (HX Edit closed).')

        def level_open():
            # The Level page: the current preset, after putting right a measurement cut short. It is a
            # tool of its own (HX Unbound menu), so it opens in its own window without the NAM-block bar.
            nonlocal leveling, level_page, look_page
            if not level_page:
                browser.send('chrome', targetBar=False, title='Level presets · HX Unbound')
                browser.send('presentation', mode='floating', focus=True)
            level_page, look_page = True, False
            from nam_level_flow import LevelFlow
            if leveling is None:
                leveling = LevelFlow(level_command, lambda: browser.actions.put({'levelChanged': True}),
                                     di=self.level_di, **self.level_files)
                self.level_flow = leveling      # closed by run() too, if browse() ends with an error
            leveling.open()
            status(leveling.view()['message'])

        def level_do(name, *args):
            from nam_level_flow import LevelError
            try:
                getattr(leveling, name)(*args)
                status(leveling.view()['message'], busy=True)
            except (LevelError, ToneError, ValueError, TypeError, KeyError) as failure:
                status(f'Level: {failure}')

        def light_event(state):
            nonlocal light, light_job
            light.update(state)
            if 'error' in state:
                light['running'] = False
                light_job = None
                status('Training cancelled.' if state.get('cancelled') else f'Training failed: {state["error"]}')
            elif state.get('done'):
                light['running'] = False
                light_job = None
                try:
                    light['result'] = self.library.add(light['output'], name=light['name'], gear=light['gear'],
                                                       source=light['source'])
                    message = f'Added "{light["name"]}" to the library. Audition it or save it to Helix.'
                except (OSError, ValueError) as error:
                    light['error'] = str(error)
                    message = f'Could not add the trained capture to the library: {error}'
                if library:
                    show_card()  # the new row
                status(message)
            else:
                status(text)

        if self.initial_look and self.library:     # opened from HX Unbound > Floor look
            try:
                look_open()
            except (OSError, ValueError) as failure:
                status(f'Floor look: {failure}')
        if self.initial_level and self.level:     # opened from the Level button
            try:
                level_open()
            except (ToneError, OSError) as failure:
                status(f'Level: {failure}')

        while (action := browser.next_action()) is not None:
            if isinstance(action, dict):
                if 'close' in action:
                    self.close_intent.set()
                    self.close_reason = action['close'] if isinstance(action['close'], str) else 'close-button'
                    break
                if action.get('inventory'):
                    status(text)
                if action.get('libraryChanged'):
                    with self._library_notification_lock:
                        if self._library_notification_pending is browser:
                            self._library_notification_pending = None
                    if library and library_signature(self.library.entries()) != library_content:
                        show_card()
                    else:
                        status(text)
                if 'navigation' in action:
                    state = action['navigation']
                    if state.get('version') == self.navigation_state.get('version'):
                        if not state.get('bound'):
                            free_slots = []
                            if not state.get('preview'):
                                slot = None
                        status(state.get('text') or '' if not state.get('bound') else text)
                if light is not None and 'light' in action:
                    light_event(action['light'])
                if optimizing is not None and 'optimize' in action:
                    optimize_event(action['optimize'])
                if listening is not None and 'listen' in action:
                    listen_event(action['listen'])
                if leveling is not None and 'levelChanged' in action:
                    view = leveling.view()
                    status(view['progress'] or view['message'], busy=bool(view['running']))
                continue
            if self.navigation and action in ('bind', 'end'):
                try:
                    reply = self.command(session, action)
                    if reply.get('error'):
                        status(reply['error'])
                    elif action == 'end':
                        if reply.get('ended'):
                            slot, free_slots, kept = None, [], None
                        status(reply.get('notice') or 'Preview ended. Choose another tone to audition.')
                    else:
                        status('Target selected. Choose a profile to audition.')
                except ToneError as error:
                    status(str(error))
                continue
            if action == 'auditionSelected' and current is not None:
                audition(current, max(0, selected))
                continue
            if isinstance(action, tuple):
                selection, error = action
                worker = None
                self._trace('selection', 'received', session)
                if error:
                    browser.send('browseError', text=error)
                    continue
                if selection is None:
                    break
                else:
                    start_selection()
                    tone_id, token = selection
                    try:
                        tone, models = self._trace_call('metadata', session, lambda: get_tone(tone_id, token))
                        converts = any(m != 'original' for m in (self.tile or {}).get('modes') or ['original'])
                        if converts and len(models) > 1:
                            # a conversion takes a while: the user picks the profile first (the card's list)
                            current, selected = (tone, models, token), -1
                            if not library:
                                browser.send('card', html=render_card(tone, models, library=bool(self.library)),
                                             has_tone=True)
                            status(f'{len(models)} profiles in this tone. Pick one under Profile to convert it.')
                        else:
                            audition((tone, models, token), 0)
                    except (ToneError, ValueError, OSError) as error:
                        status(str(error))
                    continue
            if action == 'cancel':
                self.close_reason = 'window-close'
                break
            if action == 'finish':
                self.close_reason = 'library-action-completed' if self.headless else 'close-button'
                break
            if action.startswith('replace:'):          # the taken User IR slot the user chose to audition in
                chosen = action.partition(':')[2]
                if retry and any(chosen == str(taken_slot) for taken_slot, _ in taken or []):
                    retry(int(chosen))
                continue
            if action.startswith('save:'):
                if slot is None:
                    continue
                prefix, _, destination = action.rpartition(':')
                if prefix != f'save:{trial}' or not any(destination == str(free) for free in free_slots):
                    continue
                status(f'Saving to User IR {destination}…', busy=True)
                try:
                    reply = self.command(session, 'save', slot=int(destination),
                                         **({'targetVersion': self.navigation_state.get('version')} if self.navigation else {}))
                    slot = reply.get('slot')
                    free_slots = reply.get('freeSlots', [])
                    message = ''
                    if reply.get('savedSlot'):
                        loaded_now = True
                        message = (f'Saved to this PC and loaded on the Floor in User IR {reply["savedSlot"]}. '
                                   'The preset is not saved yet.' if self.navigation else
                                   f'Saved in User IR {reply["savedSlot"]}.')
                    if reply.get('savedSlot') and self.library:
                        try:
                            if not keep():
                                message += ' It could not be added to the library on this PC.'
                            if self.navigation and kept and kept.get('id'):
                                self.library.mark_pending(kept['id'], reply['savedSlot'])
                        except (OSError, ValueError) as error:
                            message += f' Could not add it to the library: {error}'
                    if reply.get('error'):
                        message = f'{message} {reply["error"]}'.strip()
                    if reply.get('notice'):
                        message = f'{message} {reply["notice"]}'.strip()
                    status(message)
                except ToneError as error:
                    status(str(error))
                continue
            if self.library:
                command, _, argument = action.partition(':')
                identity, _, value = argument.partition(':')
                try:
                    if command == 'libraryView':
                        self.library.update_view(json.loads(argument))
                        status(text)
                    elif command in {'library', 'catalog'}:
                        if command == 'catalog' and not client_id:
                            status(KEY_HELP)
                            continue
                        library = command == 'library'
                        if level_page or look_page:
                            browser.send('chrome', targetBar=True, title='TONE3000 · HX Edit Unbound')
                        level_page = look_page = False
                        library_visibility()
                        show_card()
                    elif command == 'keep' and argument == str(trial):
                        status('Saved to your NAM library on this PC. Nothing changed on the Floor.' if keep() else
                               'This preview\'s original capture is no longer available, so nothing was added. '
                               'Preview the tone again, then choose Save to PC.')
                    elif command == 'pick' and identity in self.library.entries():
                        audition_local(identity)
                    elif self.navigation and command in ('use', 'load') and identity in self.library.entries():
                        install_local(identity, use_only=command == 'use')
                    elif self.navigation and command == 'nativeDrag' and identity in self.library.entries():
                        if self.native_drag_handler:
                            self.native_drag_handler(identity)
                    elif self.navigation and command == 'drop' and identity in self.library.entries():
                        # Dragged from this page onto HX Edit's chain: the NAM tab's drop does the rest
                        # (the block under the pointer, made a NAM block if need be, then Use or Load).
                        x, _, y = value.partition(':')
                        if not self.drop_handler or not re.fullmatch(r'-?\d{1,5}', x) or not re.fullmatch(r'-?\d{1,5}', y):
                            status('Open the NAM tab once, then drag the capture again.')
                        else:
                            self.drop_handler(identity, int(x), int(y))
                    elif self.navigation and command == 'useVersion' and identity in self.library.entries():
                        # Optimize: put the Pedal version (or the original again) on the selected NAM block
                        if not self.pedal_block:
                            status('The Pedal version needs NAM Pedal firmware on the Floor.')
                        elif value not in ('pedal', 'original'):
                            status('Unknown version.')
                        else:
                            install_local(identity, capture_version='pedal' if value == 'pedal' else None)
                    elif command == 'gear':
                        self.library.update(identity, gear=value)
                    elif command == 'instrument' and identity in self.library.entries():
                        self.library.set_instrument(identity, value or None)
                    elif command == 'icon' and identity in self.library.entries():
                        self.library.set_icon(identity, value)
                    elif command == 'iconUpload' and identity in self.library.entries():
                        try:
                            picture = base64.b64decode(value, validate=True)
                        except ValueError:
                            raise ValueError('The picture could not be read.')
                        self.library.set_icon(identity, self.library.add_picture(picture))
                    elif command == 'iconDelete':
                        self.library.remove_picture(identity)   # identity: the uploaded icon's id
                    elif command == 'light' and identity in self.library.entries() and light_job is None:
                        layers, _, quality = value.partition(':')
                        start_light(identity, layers, quality)
                    elif command == 'lightCancel' and light_job is not None:
                        light_job.cancel()
                    elif command == 'optimize' and identity in self.library.entries() and optimize_job is None:
                        start_optimize(identity)
                    elif command == 'optimizeCancel' and optimize_job is not None:
                        optimize_job.cancel()
                    elif command == 'listen' and identity in self.library.entries():
                        listen(identity, value)
                    elif command == 'listenStop' and listening is not None:
                        if listen_job is not None:
                            listen_job.cancel()
                        if player is not None:
                            player.stop()
                        listening['playing'] = None
                        status('Stopped.')
                    elif command in ('look', 'lookOpen'):
                        look_open()
                    elif command == 'lookAssign':
                        look_assign(json.loads(argument) if argument else None)
                    elif command == 'lookSave':
                        look_save()
                    elif command in ('level', 'levelOpen') and self.level:
                        try:
                            level_open()
                        except (ToneError, OSError) as failure:
                            status(f'Level: {failure}')
                    elif command == 'levelScan' and leveling:
                        spec = json.loads(argument)
                        signal = spec.get('signal')
                        level_do('scan', spec.get('presets') or [], spec.get('snapshots') or 'all',
                                 str(signal)[:40] if signal else None)
                    elif command == 'levelApply' and leveling:
                        spec = json.loads(argument)
                        # {changes, target: {value, metric}}; a bare {preset: {snapshot: dB}} still works.
                        changes = spec.get('changes', spec) if isinstance(spec, dict) else {}
                        # (not named "target": browse() has a target() of its own that status() calls)
                        leveled_to = spec.get('target') if isinstance(spec.get('target'), dict) else None
                        if leveled_to:
                            leveled_to = {'value': float(leveled_to['value']) if isinstance(leveled_to.get('value'), (int, float)) else None,
                                          'metric': 'peak' if leveled_to.get('metric') == 'peak' else 'lufs'}
                        level_do('apply', {int(k): {int(n): float(v) for n, v in snaps.items()}
                                           for k, snaps in changes.items() if k not in ('changes', 'target')}, leveled_to)
                    elif command == 'levelUndo' and leveling:
                        level_do('undo')
                    elif command == 'levelGuitarRemove' and leveling:
                        level_do('remove_guitar', argument[:40])
                    elif command == 'levelGuitar' and leveling:
                        level_do('measure_guitar', 10.0, argument[:40])
                    elif command == 'levelCancel' and leveling:
                        leveling.cancel()
                    elif command == 'versionRemove' and identity in self.library.entries():
                        if listening is not None and listening.get('id') == identity and listen_job is None:
                            if player is not None:
                                player.stop()
                            listening = None
                        if self.library.remove_version(identity, value):
                            if library:
                                show_card()
                            status('Removed the optimized version. The original capture is kept.')
                    elif command == 'remove':
                        self.library.remove(identity)
                except (OSError, ValueError, KeyError) as error:
                    status(f'Library: {error}')
            if action == 'retry' and worker is None and client_id:
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
        if light_job is not None:
            light_job.cancel()
        if optimize_job is not None:
            optimize_job.cancel()
        if listen_job is not None:
            listen_job.cancel()
        if player is not None:
            player.stop()
        if leveling is not None:
            leveling.close()      # a scan stops and puts the routing back (never left on USB 7/8)
        return slot
