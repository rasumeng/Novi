"""Per-turn network budget and policy, shared by automatic and tool retrieval."""
from contextvars import ContextVar
from contextlib import contextmanager
import time

current_session = ContextVar('novi_search_session', default=None)


class SearchSession:
    def __init__(self, authorize, stop=lambda: False, offline=False, seconds=45):
        self.authorize, self.stop, self.offline = authorize, stop, offline
        self.deadline = time.monotonic() + seconds
        self.searches = self.fetches = 0
        self.requests = set()
        self.results = []

    def check(self):
        if self.offline:
            raise PermissionError('Online lookup disabled for this request')
        if self.stop():
            raise InterruptedError('Online lookup cancelled')
        if time.monotonic() >= self.deadline:
            raise TimeoutError('Online lookup deadline exceeded')

    def reserve(self, kind, args):
        self.check()
        key = (kind, tuple(sorted(args.items())))
        if key in self.requests:
            raise ValueError('This exact retrieval was already attempted')
        if (kind == 'search' and self.searches >= 2) or (kind == 'fetch' and self.fetches >= 3):
            raise ValueError('Online lookup budget exhausted')
        if not self.authorize('web_search' if kind == 'search' else 'web_fetch', args):
            raise PermissionError('Online lookup permission was not granted')
        self.check()
        self.requests.add(key)
        if kind == 'search':
            self.searches += 1
        else:
            self.fetches += 1

    @contextmanager
    def activate(self):
        token = current_session.set(self)
        try:
            yield self
        finally:
            current_session.reset(token)
