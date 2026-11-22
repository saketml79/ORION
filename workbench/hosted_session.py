"""Process-local, verified working snapshot files, never authority or clients."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
import tempfile
from threading import RLock


class VerifiedSourceCache:
    """Private scanned source copies, never clients, credentials, or access verdicts."""

    def __init__(self, max_entries=32):
        self._lock = RLock()
        self._entries = OrderedDict()
        self.max_entries = max_entries

    def clear(self):
        with self._lock:
            for _, _, temporary in self._entries.values():
                temporary.cleanup()
            self._entries.clear()

    def discard(self, key):
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is not None:
                entry[2].cleanup()

    def contains(self, key, metadata):
        with self._lock:
            entry = self._entries.get(key)
            return entry is not None and entry[0] == metadata

    def copy(self, key, metadata, digest, size, scan, target=None):
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return False
            if entry[0] != metadata:
                self.discard(key)
                raise ValueError("Committed source metadata changed")
            try:
                with entry[1].open("rb") as source:
                    if target is None:
                        actual = scan(source, size)
                    else:
                        with Path(target).open("wb") as output:
                            actual = scan(source, size, output)
                if actual != (digest, size):
                    raise ValueError("Cached source size or checksum mismatch")
            except BaseException:
                self.discard(key)
                if target is not None:
                    Path(target).unlink(missing_ok=True)
                raise
            self._entries.move_to_end(key)
            return True

    def put(self, key, metadata, source_path, digest, size, scan, verify):
        temporary = None
        try:
            temporary = tempfile.TemporaryDirectory(prefix="workbench-verified-source-")
            destination = Path(temporary.name) / "source"
            with Path(source_path).open("rb") as source, destination.open("wb") as output:
                if scan(source, size, output) != (digest, size):
                    raise ValueError("Source changed during cache capture")
            if not verify():
                return False
            with self._lock:
                self.discard(key)
                while len(self._entries) >= self.max_entries:
                    _, old = self._entries.popitem(last=False)
                    old[2].cleanup()
                self._entries[key] = (metadata, destination, temporary)
                temporary = None
            return True
        except (OSError, MemoryError):
            return False
        finally:
            if temporary is not None:
                temporary.cleanup()


class VerifiedWorkingSnapshot:
    """One private disk-backed snapshot, pinned until all borrowers release it.

    The caller verifies the current authority and complete generation before
    borrowing. External sources have a separate metadata-validated cache.
    Cached paths are never used as mutable request working directories.
    """

    def __init__(self, max_bytes=512 * 1024 * 1024, reserve=1536 * 1024 * 1024):
        self.max_bytes = max_bytes
        self.reserve = reserve  # Compatibility only; disk admission needs no cgroup reading.
        self._lock = RLock()
        self._entry = None
        self._retired = []
        self._borrowers = 0
        self._capturing = False
        self._candidate = None
        self._epoch = 0
        self.last_admission_reason = "not_attempted"

    def _retire(self, entry):
        if entry is not None:
            if self._borrowers:
                self._retired.append(entry)
            else:
                entry[2].cleanup()

    def clear(self):
        with self._lock:
            self._epoch += 1
            entry, self._entry = self._entry, None
            self._retire(entry)

    @contextmanager
    def borrow(self, key, scan):
        with self._lock:
            entry = self._entry
            if entry is None or entry[0] != key:
                if entry is not None:
                    self._epoch += 1
                    self._entry = None
                    self._retire(entry)
                entry = None
            else:
                self._borrowers += 1
        if entry is None:
            yield None
            return
        try:
            files = {}
            for name, path, digest, size in entry[1]:
                if path.stat().st_size != size:
                    raise ValueError("Cached working snapshot checksum mismatch")
                with path.open("rb") as stream:
                    if scan(stream, size) != (digest, size):
                        raise ValueError("Cached working snapshot checksum mismatch")
                files[name] = (path, digest, size)
            yield (files, {})
        except BaseException:
            with self._lock:
                if self._entry is entry:
                    self._epoch += 1
                    self._entry = None
                    self._retire(entry)
            raise
        finally:
            with self._lock:
                self._borrowers -= 1
                if not self._borrowers:
                    retired, self._retired = self._retired, []
                    for old in retired:
                        old[2].cleanup()

    @contextmanager
    def capture_slot(self):
        """Allow at most one optional capture without waiting for a pin."""
        with self._lock:
            acquired = not self._capturing and not self._borrowers
            if acquired:
                self._capturing = True
                self._epoch += 1
                entry, self._entry = self._entry, None
                self._retire(entry)
                self.last_admission_reason = "capture_started"
            else:
                self.last_admission_reason = "capture_busy"
        try:
            yield acquired
        finally:
            if acquired:
                with self._lock:
                    candidate, self._candidate = self._candidate, None
                    self._capturing = False
                    if candidate is not None:
                        candidate[2].cleanup()

    def prepare(self, key, staging, names, objects, scan, memory_state=None):
        """Copy and scan a candidate, but do not expose it before publication."""
        with self._lock:
            if not self._capturing or self._borrowers or self._candidate is not None:
                self.last_admission_reason = "capture_busy"
                return None
            epoch = self._epoch
        temporary = None
        try:
            paths = [(name, staging / name) for name in sorted(names)]
            sizes = [(name, path, path.stat().st_size) for name, path in paths]
            cost = sum(size + len(name.encode("utf-8")) + 256 for name, _, size in sizes)
            if cost > self.max_bytes:
                self.last_admission_reason = "snapshot_exceeds_generation_limit"
                return None
            temporary = tempfile.TemporaryDirectory(prefix="workbench-verified-snapshot-")
            root = Path(temporary.name)
            files = []
            for name, path, size in sizes:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with path.open("rb") as source, target.open("wb") as output:
                    digest, actual_size = scan(source, size, output)
                if actual_size != size:
                    self.last_admission_reason = "snapshot_changed_during_capture"
                    return None
                files.append((name, target, digest, size))
            candidate = (key, tuple(files), temporary, epoch)
            with self._lock:
                if not self._capturing or self._borrowers or self._epoch != epoch:
                    self.last_admission_reason = "capture_invalidated"
                    return None
                self._candidate = candidate
                self.last_admission_reason = "prepared"
                temporary = None
                return candidate
        except (OSError, MemoryError, ValueError):
            self.last_admission_reason = "snapshot_not_cacheable"
            return None
        finally:
            if temporary is not None:
                temporary.cleanup()

    def publish(self, candidate):
        """Publish only the candidate owned by the active capture lease."""
        with self._lock:
            if (candidate is None or candidate is not self._candidate or not self._capturing
                    or self._borrowers or candidate[3] != self._epoch):
                return False
            self._entry = candidate
            self._candidate = None
            self.last_admission_reason = "ready"
            return True

    def capture(self, key, staging, names, objects, scan, memory_state=None):
        return self.publish(self.prepare(key, staging, names, objects, scan, memory_state))