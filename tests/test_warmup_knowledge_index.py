"""Startup must not block the first connection on a full knowledge re-index.

``warmup()`` used to call ``init_knowledge_index()`` unconditionally, and that
runs ``index_all()`` -- embedding every document through Ollama with a 60s
per-request timeout. Because the websocket handler builds the backend lazily,
the *first* chat connection paid for that whole job before it could handshake.

With ``memory.enabled = false`` the index is not readable anyway (search tools
are withheld), so the eager build is pure cost.
"""

from novi.services import context as context_mod


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args[0] if args else None)
        return None


def _context(memory_enabled):
    class Fake:
        def __init__(self):
            self.config = {"memory": {"enabled": memory_enabled},
                           "workspace": {"knowledge": "~/.novi/knowledge"}}
            self._knowledge_inited = False
            self._brain = None
            self._vault = None

        # Only these two are touched by the code path under test.
        @property
        def brain(self):
            return None

        @property
        def memory(self):
            return None

        @property
        def project_index(self):
            return None

        @property
        def embedding_service(self):
            return None

        @property
        def model_service(self):
            return None

        @property
        def simple_llm(self):
            return None

        @property
        def scheduler(self):
            return None

        @property
        def _brain_event_bus(self):
            return None

        @property
        def reranker_service(self):
            return None

        def recover_jobs(self, bus=None):
            return None

        init_knowledge_index = context_mod.NoviContext.init_knowledge_index

    return Fake()


def _run(memory_enabled, monkeypatch):
    import novi.memory.knowledge_index as ki_mod

    recorder = _Recorder()
    # init_knowledge_index is imported *inside* the method, so the patch has to
    # target the defining module rather than novi.services.context.
    monkeypatch.setattr(ki_mod, "init_knowledge_index", recorder)
    ctx = _context(memory_enabled)
    from novi.services.context import NoviContext
    NoviContext.warmup(ctx)
    return recorder.calls


def test_knowledge_index_is_not_eagerly_built_when_memory_is_disabled(monkeypatch):
    calls = _run(False, monkeypatch)
    assert calls == []


def test_knowledge_index_is_still_built_when_memory_is_enabled(monkeypatch):
    calls = _run(True, monkeypatch)
    assert len(calls) == 1