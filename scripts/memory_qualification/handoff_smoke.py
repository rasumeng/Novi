"""Measure selected-model memory cancellation on an owned local Ollama server.

This never constructs Brain or writes a vault. It only stops the server it starts.
"""
import argparse
import json
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace

from novi.brain.curation.contracts import Review
from novi.providers.memory import OllamaMemoryClient
from novi.services.inference_coordinator import InferenceCoordinator
from .run import OwnedServer, api


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ollama-exe', type=Path, required=True)
    parser.add_argument('--model', required=True)
    args = parser.parse_args()
    owner = OwnedServer(args.ollama_exe)
    try:
        owner.start()
        if not any(row['name'] == args.model for row in api('tags')['models']):
            raise RuntimeError(f'{args.model} is not installed; no pull attempted')
        coordinator = InferenceCoordinator()
        client = OllamaMemoryClient(SimpleNamespace(provider='ollama', model=args.model,
            config={'url': 'http://127.0.0.1:11439'}), context=4096, output=256,
            timeout=90, headroom=lambda: True)
        started, released = Event(), Event()
        result = {}

        def memory():
            with coordinator.try_acquire_memory(idle_seconds=0) as cancel:
                if cancel is None:
                    raise RuntimeError('Memory lease was not admitted')
                started.set()
                try:
                    client.generate('Review the supplied memory proposal. Return Review JSON.',
                        {'packet': {'turns': [{'id': 't', 'actor': 'user',
                            'text': 'For Novi I prefer local models.' * 20}], 'notes': []},
                         'proposal': {'outcome': 'abstain', 'operations': [], 'reason': 'Test'}},
                        Review, cancel)
                    result['memory'] = 'completed before cancellation'
                except Exception as exc:
                    result['memory'] = f'{type(exc).__name__}: {exc}'
                finally:
                    released.set()

        thread = Thread(target=memory, daemon=True)
        thread.start()
        if not started.wait(2):
            raise RuntimeError('Memory call did not start')
        sleep(.5)
        demand = monotonic()
        try:
            with coordinator.acquire_foreground(timeout=15):
                result['handoff_seconds'] = round(monotonic() - demand, 3)
                result['memory_released_before_foreground'] = released.is_set()
                chat_started = monotonic()
                reply = api('chat', {'model': args.model, 'stream': False, 'think': False,
                    'messages': [{'role': 'user', 'content': 'Reply with one word: ready'}],
                    'options': {'num_predict': 16}}, timeout=90)
                result['foreground_reply_seconds'] = round(monotonic() - chat_started, 3)
                result['foreground_done'] = bool(reply.get('done'))
        finally:
            thread.join(5)
        print(json.dumps(result, indent=2))
        if not result.get('memory_released_before_foreground') or not result.get('foreground_done'):
            raise RuntimeError('Foreground handoff was not confirmed')
    finally:
        try:
            owner.stop()
        except RuntimeError:
            # Windows taskkill can report a child error after the owned server
            # has already exited. Accept cleanup only when the parent is gone
            # and the reserved test port is free; never touch shared Ollama.
            import socket
            for _ in range(50):
                if owner.process is not None and owner.process.poll() is not None:
                    break
                sleep(.1)
            if owner.process is None or owner.process.poll() is None:
                raise
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 11439))
            if owner.log:
                owner.log.close()


if __name__ == '__main__':
    main()
