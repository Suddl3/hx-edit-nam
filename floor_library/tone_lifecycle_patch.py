"""Local, reviewable upstream overlay: completed notices must not own a session."""


def patch_preview(source):
    replacements = (
        ('from threading import Thread', 'from threading import Lock, Thread, current_thread'),
        ('        self.thread = None\n', '        self.thread = None\n        self.lifecycle_lock = Lock()\n'),
        ('''        elif not self.thread or not self.thread.is_alive():
            self.session = payload['session']
            self.bounds = payload['bounds']
            self.thread = Thread(target=self.run, args=(self.session, payload['window']), daemon=True)
            self.thread.start()
        else:
            self.script.post({'type': 'toneCommand', 'payload': {
                'session': payload['session'], 'action': 'close', 'id': 0}})
''', '''        else:
            with self.lifecycle_lock:
                if not self.thread or not self.thread.is_alive():
                    self.session = payload['session']
                    self.bounds = payload['bounds']
                    self.thread = Thread(target=self.run, args=(self.session, payload['window']), daemon=True)
                    self.thread.start()
                    return
            self.script.post({'type': 'toneCommand', 'payload': {
                'session': payload['session'], 'action': 'close', 'id': 0}})
'''),
        ('''            if notice:
                self.message(*notice)
''', '''            # Device cleanup and close acknowledgement have finished. A modal
            # notice can remain open indefinitely; it must not reject new starts.
            # An old notice thread must never clear a newer session's ownership.
            with self.lifecycle_lock:
                if self.thread is current_thread():
                    self.thread = None
            if notice:
                self.message(*notice)
'''),
    )
    for old, new in replacements:
        if source.count(old) != 1:
            raise ValueError('Upstream TonePreview changed; review the lifecycle overlay')
        source = source.replace(old, new)
    return source
