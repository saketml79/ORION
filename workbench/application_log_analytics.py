"""Read-only measurements of this Python process, never a user or fleet metric."""
import os
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

_INSTANCE = uuid4().hex


def _windows_counters():
    import ctypes
    from ctypes import wintypes

    class Memory(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    *[(name, ctypes.c_size_t) for name in (
                        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                        "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]]

    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessIoCounters.argtypes = [wintypes.HANDLE, ctypes.POINTER(IO)]
    kernel.GetProcessIoCounters.restype = wintypes.BOOL
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    handle, memory, io = kernel.GetCurrentProcess(), Memory(), IO()
    memory.cb = ctypes.sizeof(memory)
    return (memory.WorkingSetSize if psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb) else None,
            (io.ReadTransferCount, io.WriteTransferCount) if kernel.GetProcessIoCounters(handle, ctypes.byref(io)) else (None, None),
            "Windows process I/O transfer bytes, including non-disk I/O")


def _linux_counters():
    memory, read_bytes, write_bytes = None, None, None
    try:
        with open("/proc/self/statm", encoding="ascii") as stream:
            memory = int(stream.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        pass
    try:
        with open("/proc/self/io", encoding="ascii") as stream:
            values = dict(line.split(":", 1) for line in stream if ":" in line)
        read_bytes, write_bytes = int(values["read_bytes"]), int(values["write_bytes"])
    except (OSError, ValueError, KeyError):
        pass
    return memory, (read_bytes, write_bytes), "Linux process storage read_bytes and write_bytes"


def process_sample():
    memory, io, source = None, (None, None), "Process I/O counters unavailable on this platform"
    try:
        if sys.platform == "win32":
            memory, io, source = _windows_counters()
        elif sys.platform.startswith("linux"):
            memory, io, source = _linux_counters()
    except (OSError, AttributeError):
        pass
    return {"instance": _INSTANCE, "timestamp": datetime.now(timezone.utc).isoformat(),
            "monotonic_seconds": time.monotonic(), "cpu_seconds": time.process_time(),
            "resident_bytes": memory, "io_read_bytes": io[0], "io_write_bytes": io[1],
            "io_source": source, "scope": "current shared application process, not per user"}


def install_application_log_analytics(app):
    from fastapi.responses import JSONResponse

    @app.get("/api/application-log-analytics/process")
    def application_process_metrics():
        return JSONResponse(process_sample(), headers={"Cache-Control": "no-store"})