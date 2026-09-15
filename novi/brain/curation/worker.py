"""One context-owned daemon: eligibility, leases, evidence and truthful events."""
import logging
import json
from threading import Event, Lock, Thread
from time import time
from uuid import uuid4
from ...models.service import ModelUnavailableError
from .pipeline import curate

log = logging.getLogger(__name__)
MAX_AUTOMATIC_ATTEMPTS = 3


class MemoryWorker:
    def __init__(self, brain, jobs, model_service, config, *, client_factory=None,
                 headroom=lambda: False):
        self.brain, self.jobs, self.models = brain, jobs, model_service
        self.config, self.headroom = config, headroom
        self.client_factory = client_factory or model_service.memory_client
        self.stop = Event()
        self.wake = Event()
        self._running = Lock()
        self._state_lock = Lock()
        self._version = 0
        self._instance_id = uuid4().hex
        self._paused = bool(jobs.control('paused'))
        self._thread = None
        self._active_cancel = None
        memory_cfg = config().get('memory', {})
        automatic = bool(memory_cfg.get('enabled', True) and memory_cfg.get('automatic_updates', True))
        state = 'paused' if self._paused else 'idle' if automatic else 'disabled'
        reason = ('Automatic memory is paused' if self._paused else
                  '' if automatic else
                  'Memory is off; new turns are not queued for updates' if not memory_cfg.get('enabled', True) else
                  'Periodic memory updates are off; permitted turns remain pending')
        self._status = {'state': state, 'version': 0, 'instance_id': self._instance_id, 'mode': 'shadow', 'job_id': None,
                        'reason': reason, 'note_ids': []}

    def snapshot(self):
        with self._state_lock:
            return dict(self._status)

    def history(self):
        entries = []
        for row in self.jobs.recent():
            result = json.loads(row.pop('result') or 'null') or {}
            changes = []
            for op in result.get('proposal', {}).get('operations', []):
                saved = self.jobs.operation(row['id'], op['id'])
                before = saved.get('before') if saved else None
                if before and before.startswith('---'):
                    before = before.split('---', 2)[-1].strip()
                changes.append({'action': op['action'], 'text': op['markdown'], 'before': before,
                                'evidence': [e['quote'] for e in op['evidence']],
                                'note_id': saved.get('note_id') if saved else None})
            entries.append(dict(row, changes=changes))
        return entries

    def _emit(self, state, job=None, **data):
        with self._state_lock:
            self._version += 1
            self._status = {'state': state, 'version': self._version,
                            'instance_id': self._instance_id,
                            'job_id': job['id'] if job else None,
                            'conversation_id': job['conversation_id'] if job else None,
                            'mode': job['mode'] if job else 'shadow',
                            'note_ids': [], 'reason': '', 'updated': time(), **data}
            snapshot = dict(self._status)
        self.brain.emit_memory_activity(snapshot)

    def pause(self, paused=True):
        self._paused = paused
        self.jobs.control('paused', paused)
        if paused:
            with self._state_lock:
                if self._active_cancel is not None:
                    self._active_cancel.set()
            self.models.inference.pause_memory()
        self._emit('paused' if paused else 'idle')
        self.wake.set()

    def configuration_changed(self):
        """Apply a live toggle before any in-flight job can write."""
        cfg = self.config().get('memory', {})
        if not cfg.get('enabled', True) or not cfg.get('automatic_updates', True):
            with self._state_lock:
                if self._active_cancel is not None:
                    self._active_cancel.set()
            self.models.inference.pause_memory()
        self.wake.set()

    def start(self):
        if self._thread is None:
            self._thread = Thread(target=self._loop, daemon=True, name='novi-memory')
            self._thread.start()

    def close(self):
        self.stop.set()
        with self._state_lock:
            if self._active_cancel is not None:
                self._active_cancel.set()
        self.models.inference.pause_memory()
        self.wake.set()
        # Never join inference while closing the application. Durable leases
        # recover on restart if cancellation cannot complete before process exit.

    def _loop(self):
        while not self.stop.is_set():
            self.wake.wait(5)
            self.wake.clear()
            try:
                self.run_once()
            except Exception:
                log.exception('Memory worker iteration failed')

    def run_once(self, *, manual_shadow=False):
        cfg = self.config().get('memory', {})
        if self.stop.is_set() or self._paused:
            return None
        if not cfg.get('enabled', True):
            if self.snapshot().get('state') != 'disabled':
                self._emit('disabled', reason='Memory is off; permitted turns are not queued for updates')
            return None
        if not manual_shadow and not cfg.get('automatic_updates', True):
            if self.snapshot().get('state') != 'disabled':
                self._emit('disabled', reason='Periodic memory updates are off; permitted turns remain pending')
            return None
        if not self._running.acquire(blocking=False):
            return None
        job = None
        try:
            try:
                resolved = self.models.resolve_primary_snapshot()
            except ModelUnavailableError as exc:
                self._emit('unavailable', reason=str(exc))
                return None
            if not self.headroom():
                self._emit('deferred', reason='Waiting for memory headroom')
                return None
            with self.models.inference.try_acquire_memory(idle_seconds=0 if manual_shadow else 60) as cancel:
                if cancel is None:
                    return None
                try:
                    client = self.client_factory(resolved, context=cfg.get('context_tokens', 16384),
                                                 output=1800, timeout=300, headroom=self.headroom)
                except ValueError as exc:
                    self._emit('unavailable', reason=str(exc))
                    return None
                with self._state_lock:
                    self._active_cancel = cancel
                    if self._paused or self.stop.is_set():
                        cancel.set()
                if cancel.is_set():
                    return None
                mode = 'shadow' if manual_shadow else 'apply'
                job = self.jobs.claim(mode=mode)
                if job is None:
                    return None
                self._emit('proposing', job)
                saved = json.loads(job['result']) if job.get('result') else None
                packet = job['packet'] if saved and saved.get('state') == 'approved' else self.brain.memory_packet(job['packet'])
                def reviewing():
                    self.jobs.stage(job['id'], 'verifying')
                    self._emit('verifying', job)
                if saved and saved.get('state') == 'approved':
                    result = saved  # Replay the approved delta, never a new guess.
                else:
                    self.jobs.stage(job['id'], 'verifying', packet=packet)
                    result = curate(packet, client, cancel, reviewing)
                result.setdefault('model', {'provider': resolved.provider, 'model': resolved.model})
            # Storage/reconciliation does not retain the model's inference slot.
            state = result['state']
            note_ids = []
            if state == 'approved':
                self.jobs.stage(job['id'], 'approved', result=result)
                if mode == 'shadow':
                    state = 'shadow'
                else:
                    current = self.config().get('memory', {})
                    if (cancel.is_set() or self.stop.is_set() or self._paused or
                            not current.get('enabled', True) or not current.get('automatic_updates', True) or
                            self.models.inference.foreground_active):
                        raise InterruptedError('Memory paused before saving')
                    self.jobs.stage(job['id'], 'applying')
                    self._emit('applying', job)
                    # Foreground demand can arrive after the inference lease is
                    # released. Recheck between journaled writes as well.
                    class ApplyCancellation:
                        def is_set(inner):
                            live = self.config().get('memory', {})
                            return (cancel.is_set() or self.stop.is_set() or self._paused or
                                    self.models.inference.foreground_active or
                                    not live.get('enabled', True) or not live.get('automatic_updates', True))
                    note_ids = self.brain.apply_memory(job, result, packet, self.jobs, ApplyCancellation())
                    state = 'applied'
            if state == 'deferred' and not manual_shadow and job['attempts'] >= MAX_AUTOMATIC_ATTEMPTS:
                state = 'parked'
                result = dict(result, state='parked', reason=(result.get('reason') or
                    'No safe memory change was found') + '; parked until new conversation evidence arrives')
            self.jobs.finish(job, state, result)
            self._emit(state, job, note_ids=note_ids, reason=result.get('reason', ''))
            return result
        except Exception as exc:
            if job:
                # Preserve approved results needed for crash-safe replay.
                stored = next((r for r in self.jobs.recent(100) if r['id'] == job['id']), None)
                previous = json.loads(stored['result']) if stored and stored['result'] else None
                terminal = ('parked' if not manual_shadow and
                            job['attempts'] >= MAX_AUTOMATIC_ATTEMPTS else 'deferred')
                self.jobs.finish(job, terminal, previous, error=str(exc))
            else:
                terminal = 'deferred'
            reason = str(exc)
            if terminal == 'parked':
                reason += '; parked until new conversation evidence arrives'
            self._emit(terminal, job, reason=reason)
            return {'state': terminal, 'reason': reason}
        finally:
            with self._state_lock:
                self._active_cancel = None
            self._running.release()
