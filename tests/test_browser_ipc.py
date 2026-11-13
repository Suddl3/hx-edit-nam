"""Bounded browser IPC using Python threads and fake pipes/processes only."""
import json
import subprocess
from threading import Event, Thread
from time import monotonic
from unittest.mock import patch

import pytest

from tone3000 import ToneError
from tone_browser import ToneBrowser


class Clock:
    def __init__(self):
        self.value = 100.0

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class Pipe:
    def __init__(self, process, blocked=False):
        self.process = process
        self.started = Event()
        self.unblock = Event()
        if not blocked:
            self.unblock.set()
        self.lines = []
        self.closed = False

    def write(self, line):
        self.started.set()
        # A pipe that ignores the browser's deadlines. Only process shutdown
        # releases this simulated kernel write, just like a dead helper pipe.
        if not self.unblock.wait(2):
            raise AssertionError('test cleanup failed to unblock fake pipe')
        if self.process.exited.is_set():
            raise BrokenPipeError('fake helper stopped')
        self.lines.append(json.loads(line))
        if self.lines[-1]['type'] == 'close':
            self.process.finish()

    def flush(self):
        pass

    def close(self):
        self.closed = True


class Process:
    def __init__(self, blocked=False, needs_kill=False):
        self.exited = Event()
        self.stdin = Pipe(self, blocked)
        self.needs_kill = needs_kill
        self.calls = []

    def poll(self):
        return 0 if self.exited.is_set() else None

    def finish(self):
        self.exited.set()
        self.stdin.unblock.set()

    def terminate(self):
        self.calls.append('terminate')
        if not self.needs_kill:
            self.finish()

    def kill(self):
        self.calls.append('kill')
        self.finish()

    def wait(self, timeout):
        assert timeout is not None and timeout > 0
        self.calls.append(('wait', timeout))
        if not self.exited.is_set():
            # Controlled fake timeout: no real delay is needed for escalation.
            raise subprocess.TimeoutExpired('fake ToneBrowser', timeout)
        return 0


def browser_for(process, *, queue_limit=64):
    browser = ToneBrowser.__new__(ToneBrowser)
    browser.window_mode = 'floating'
    browser.detached = Event()
    browser.process = process
    browser.queue_limit = queue_limit
    browser.monitor_interval = 0.005
    browser.close_timeout = 0.05
    browser.terminate_timeout = 0.02
    browser.kill_timeout = 0.02
    browser.join_timeout = 0.01
    browser._initialize_io()
    browser.writer = Thread(target=browser._write_commands, daemon=True)
    browser.monitor = Thread(target=browser._monitor_process, daemon=True)
    browser.reader = Thread(target=lambda: None, daemon=True)
    browser.reader.start()
    browser.writer.start()
    browser.monitor.start()
    return browser


def finish(browser):
    browser.process.finish()
    browser._io_wake.set()
    browser.close()
    assert not browser.writer.is_alive()
    assert not browser.monitor.is_alive()


def test_commands_remain_ordered_and_close_drains_before_stdin_closes():
    process = Process()
    browser = browser_for(process)
    try:
        assert browser.send('card', html='first')
        assert browser.send('target', label='second')
        assert browser.send('status', text='third')
        browser.close()
        assert [line['type'] for line in process.stdin.lines] == ['card', 'target', 'status', 'close']
        assert browser.error is None
        assert process.stdin.closed
        assert process.calls == []
        assert not browser.send('focus')
    finally:
        finish(browser)


def test_stalled_write_does_not_block_concurrent_send_and_watchdog_fails_visibly():
    process, clock = Process(blocked=True), Clock()
    with patch('tone_browser.monotonic', clock.now):
        browser = browser_for(process)
        try:
            assert browser.send('card', html='blocked')
            assert process.stdin.started.wait(0.2)
            returned = Event()

            def send_more():
                assert browser.send('target', label='queued')
                assert browser.send('focus')
                returned.set()

            caller = Thread(target=send_more, daemon=True)
            caller.start()
            assert returned.wait(0.2), 'send must not wait behind a blocked pipe writer'
            caller.join(timeout=0.1)
            clock.advance(browser.write_timeout + 1)
            browser._io_wake.set()
            assert browser._shutdown_done.wait(0.2)
            assert 'stopped responding' in browser.error
            assert 'unconfirmed' in browser.error
            assert browser._commands.empty()
            assert process.stdin.lines == [], 'queued commands must not run after terminal failure'
            assert not browser.send('focus')
            with pytest.raises(ToneError, match='stopped responding'):
                browser.next_action()
            browser.ready.set()
            with pytest.raises(ToneError, match='stopped responding'):
                browser.wait_ready()
        finally:
            finish(browser)


def test_full_queue_cancels_pending_commands_with_explicit_terminal_error():
    process = Process(blocked=True)
    browser = browser_for(process, queue_limit=2)
    try:
        assert browser.send('card', html='blocked')
        assert process.stdin.started.wait(0.2)
        assert browser.send('target', label='one')
        assert browser.send('focus')
        assert not browser.send('status', text='cannot fit')
        assert 'queue filled' in browser.error
        assert browser._commands.empty()
        assert browser._shutdown_done.wait(0.2)
        assert process.stdin.lines == []
        failure = browser.error
        browser.close()
        assert browser.error == failure, 'shutdown must preserve the first IPC failure'
        with pytest.raises(ToneError, match='queue filled'):
            browser.next_action()
    finally:
        finish(browser)


def test_close_is_bounded_with_stalled_pipe_and_escalates_through_terminate_kill():
    process, clock = Process(blocked=True, needs_kill=True), Clock()
    with patch('tone_browser.monotonic', clock.now):
        browser = browser_for(process)
        try:
            assert browser.send('card', html='blocked')
            assert process.stdin.started.wait(0.2)
            done = Event()
            closer = Thread(target=lambda: (browser.close(), done.set()), daemon=True)
            start = monotonic()
            closer.start()
            # close() signals this event after its deadline and ordered close
            # command have been installed. Advance the fake clock, not a sleep.
            deadline = monotonic() + 0.2
            while browser._close_deadline is None and monotonic() < deadline:
                Event().wait(0.001)
            assert browser._close_deadline is not None
            clock.advance(browser.close_timeout + 0.001)
            browser._io_wake.set()
            assert done.wait(0.3)
            assert monotonic() - start < 0.5
            closer.join(timeout=0.1)
            assert process.calls == ['terminate', ('wait', browser.terminate_timeout),
                                     'kill', ('wait', browser.kill_timeout)]
            assert process.exited.is_set()
            assert not browser.writer.is_alive()
            assert browser._commands.empty()
            assert 'did not close in time' in browser.error
        finally:
            finish(browser)


def test_pipe_error_is_visible_and_close_is_idempotent():
    process = Process(blocked=True)
    browser = browser_for(process)
    try:
        assert browser.send('card', html='pending')
        assert process.stdin.started.wait(0.2)
        # Make the fake stream raise without completing the helper process.
        def broken(_line):
            raise BrokenPipeError('private pipe failure detail')
        process.stdin.write = broken
        process.stdin.unblock.set()
        assert browser.send('focus')
        assert browser._shutdown_done.wait(0.2)
        assert 'communication failed' in browser.error
        assert 'private pipe' not in browser.error
        browser.close()
        calls = list(process.calls)
        browser.close()
        assert process.calls == calls
    finally:
        finish(browser)
