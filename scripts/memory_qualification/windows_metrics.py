"""Windows process-tree sampling without a new production dependency.

Working set and private commit are distinct. Shared pages may be double-counted
across processes; file-backed bytes are explicitly unavailable here.
"""
import ctypes as c
from ctypes import wintypes as w
import os
import threading
import time


class Memory(c.Structure):
    _fields_ = [('length', w.DWORD), ('load', w.DWORD)] + [(n, c.c_ulonglong) for n in
        ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')]


def system_memory():
    m = Memory(); m.length = c.sizeof(m)
    if not c.windll.kernel32.GlobalMemoryStatusEx(c.byref(m)):
        raise OSError('GlobalMemoryStatusEx failed')
    return {'total': m.total, 'available': m.available, 'load_percent': m.load}


class Entry(c.Structure):
    _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('pid', w.DWORD),
                ('heap', c.c_size_t), ('module', w.DWORD), ('threads', w.DWORD),
                ('parent', w.DWORD), ('priority', w.LONG), ('flags', w.DWORD),
                ('exe', w.WCHAR * 260)]


class Counters(c.Structure):
    _fields_ = [('cb', w.DWORD), ('faults', w.DWORD)] + [(n, c.c_size_t) for n in
        ('peak_ws', 'ws', 'peak_pool_paged', 'pool_paged', 'peak_pool_nonpaged',
         'pool_nonpaged', 'pagefile', 'peak_pagefile', 'private')]


def processes():
    k = c.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = w.HANDLE
    k.CloseHandle.argtypes = [w.HANDLE]
    k.Process32FirstW.argtypes = [w.HANDLE, c.POINTER(Entry)]
    k.Process32NextW.argtypes = [w.HANDLE, c.POINTER(Entry)]
    handle = k.CreateToolhelp32Snapshot(2, 0)
    if handle == c.c_void_p(-1).value:
        raise OSError('process snapshot failed')
    try:
        e = Entry(); e.size = c.sizeof(e)
        result = []
        ok = k.Process32FirstW(handle, c.byref(e))
        while ok:
            result.append((e.pid, e.parent, e.exe))
            ok = k.Process32NextW(handle, c.byref(e))
        return result
    finally:
        k.CloseHandle(handle)


def tree_metrics(root):
    rows = processes(); pids = {root}
    while True:
        expanded = pids | {pid for pid, parent, _ in rows if parent in pids}
        if expanded == pids: break
        pids = expanded
    k = c.windll.kernel32
    k.OpenProcess.restype = w.HANDLE
    k.GetProcessTimes.argtypes = [w.HANDLE] + [c.POINTER(w.FILETIME)] * 4
    c.windll.psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, c.POINTER(Counters), w.DWORD]
    ws = private = 0; cpu = 0.; failures = []
    for pid in pids:
        handle = k.OpenProcess(0x410, False, pid)
        if not handle:
            failures.append(pid); continue
        try:
            m = Counters(); m.cb = c.sizeof(m)
            if not c.windll.psapi.GetProcessMemoryInfo(handle, c.byref(m), m.cb):
                failures.append(pid); continue
            ws += m.ws; private += m.private
            times = [w.FILETIME() for _ in range(4)]
            if not k.GetProcessTimes(handle, *(c.byref(t) for t in times)):
                failures.append(pid)
            else:
                cpu += sum((t.dwHighDateTime << 32) + t.dwLowDateTime for t in times[2:]) / 1e7
        finally:
            k.CloseHandle(handle)
    # A runner may exit between the snapshot and OpenProcess during normal unload.
    # Only unreadable processes that still exist are telemetry failures.
    if failures:
        live = {pid for pid, _, _ in processes()}
        failures = [pid for pid in failures if pid in live]
    return {'working_set': ws, 'private_commit': private, 'cpu_seconds': cpu,
            'pids': sorted(pids), 'unreadable_pids': failures}


class Monitor:
    def __init__(self, pid, reserve, peak_cap, deadline, stop_file):
        self.pid, self.reserve, self.peak_cap = pid, reserve, peak_cap
        self.deadline, self.stop_file = deadline, stop_file
        self.samples = []; self.failure = None; self.done = threading.Event()
        self.baseline = tree_metrics(pid)
        self.idle_server = dict(self.baseline)
        # This server was launched solely for evaluation. Include its memory,
        # rather than hiding that cost by subtracting its idle footprint.
        self.baseline['working_set'] = 0
        self.baseline['private_commit'] = 0

    def __enter__(self):
        self.thread = threading.Thread(target=self.loop, daemon=True); self.thread.start()
        return self

    def loop(self):
        previous = time.perf_counter()
        while not self.done.is_set():
            if self.failure:
                self.done.wait(.1)
                continue
            try:
                now = time.perf_counter(); m = tree_metrics(self.pid)
                m.update(system_memory()); m['time'] = now
                m['sampler_delay_seconds'] = max(0, now - previous - .1)
                previous = now; self.samples.append(m)
                if m['unreadable_pids']: self.failure = 'process telemetry unavailable'
                if m['available'] < self.reserve: self.failure = 'memory pressure'
                if max(m['working_set'] - self.baseline['working_set'],
                       m['private_commit'] - self.baseline['private_commit']) > self.peak_cap:
                    self.failure = 'incremental memory cap exceeded'
                if now > self.deadline: self.failure = 'job deadline'
                if self.stop_file.exists(): self.failure = 'foreground stop requested'
            except Exception as exc:
                self.failure = repr(exc)
            self.done.wait(.1)

    def __exit__(self, *args):
        self.done.set(); self.thread.join(2)

    def summary(self):
        return {'baseline': self.baseline, 'samples': self.samples,
                'idle_server': self.idle_server,
                'memory_accounting': 'whole owned server tree; zero memory baseline',
                'failure': self.failure, 'file_backed_bytes': None,
                'foreground_chat_slowdown': None,
                'note': 'Sampler delay is a synthetic scheduling probe, not foreground chat latency.'}
