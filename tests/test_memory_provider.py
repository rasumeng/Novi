import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace
import pytest
from novi.providers.memory import OllamaMemoryClient
from novi.brain.curation.contracts import Review


def resolved(url='http://127.0.0.1:11434'):
    return SimpleNamespace(provider='ollama', model='selected-model', config={'url': url})


def test_remote_endpoint_and_incomplete_output_are_not_fallbacks():
    with pytest.raises(ValueError, match='local Ollama'):
        OllamaMemoryClient(resolved('https://remote.example'))
    cancel = Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        OllamaMemoryClient(resolved()).generate('', {}, Review, cancel)


def test_cancellation_interrupts_transport_without_stopping_server():
    started, release, finished = Event(), Event(), Event()
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            started.set()
            release.wait(3)
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')
            except OSError:
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    cancel = Event()
    errors = []
    client = OllamaMemoryClient(resolved(f'http://127.0.0.1:{server.server_port}'))
    def call():
        try:
            client.generate('Review evidence.', {}, Review, cancel)
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()
    thread = Thread(target=call, daemon=True)
    thread.start()
    try:
        assert started.wait(2)
        cancel.set()
        assert finished.wait(1)
        assert isinstance(errors[0], InterruptedError)
        assert seen[0]['model'] == 'selected-model'
        assert seen[0]['format'] == Review.model_json_schema()
        assert 'Review evidence.' in seen[0]['messages'][1]['content']
        assert 'UNTRUSTED DATA' in seen[0]['messages'][1]['content']
        assert server_thread.is_alive()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(1)

    assert seen[0]['keep_alive'] == '10m'
