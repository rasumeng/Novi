"""Available-memory admission; unknown telemetry never means free memory."""
import sys


def available_memory():
    try:
        import psutil
        return psutil.virtual_memory().available
    except ImportError:
        if sys.platform != 'win32':
            return None
    try:
        import ctypes
        class Status(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in ('total', 'available', 'page_total',
                                                       'page_available', 'virtual_total', 'virtual_available', 'extended')]
        status = Status()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.available
    except (OSError, AttributeError):
        pass
    return None


def has_headroom(reserve_bytes=4 * 1024**3, incremental_bytes=0):
    available = available_memory()
    return available is not None and available >= reserve_bytes + incremental_bytes
