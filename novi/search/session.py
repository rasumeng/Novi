"""Per-turn network budget and policy, shared by automatic and tool retrieval."""
from contextvars import ContextVar
from contextlib import contextmanager
import time

current_session = ContextVar('novi_search_session', default=None)
_approved_request = ContextVar('novi_approved_search_request', default=None)


class SearchSession:
    def __init__(self, authorize, stop=lambda: False, offline=False, seconds=45):
        self.authorize, self.stop, self.offline = authorize, stop, offline
        self.seconds = seconds
        self.deadline = float('inf')
        self.searches = self.fetches = 0
        self.requests = set()
        self.results = []
        self.denied = False

    def check(self):
        if self.denied:
            raise PermissionError('Online lookup permission was not granted')
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
        approved = _approved_request.get()
        tool = 'web_search' if kind == 'search' else 'web_fetch'
        identity = 'query' if kind == 'search' else 'url'
        already_allowed = approved is not None and approved[0] == tool and approved[1].get(identity) == args.get(identity)
        if not already_allowed and not self.authorize(tool, args):
            self.denied = True
            raise PermissionError('Online lookup permission was not granted')
        if self.deadline == float('inf'):
            self.deadline = time.monotonic() + self.seconds
        self.check()
        self.requests.add(key)
        if kind == 'search':
            self.searches += 1
        else:
            self.fetches += 1

    @contextmanager
    def activate(self, approved=None):
        token = current_session.set(self)
        permission_token = _approved_request.set(approved)
        try:
            yield self
        finally:
            _approved_request.reset(permission_token)
            current_session.reset(token)


def request_timeout(default):
    session = current_session.get()
    if session is None:
        return default
    session.check()
    return min(default, max(.01, session.deadline - time.monotonic()))
