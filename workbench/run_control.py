"""Process-local, cooperative admission control. Never interrupts in-flight work."""
from datetime import datetime, timezone
from threading import Condition, Event


def _now():
    return datetime.now(timezone.utc).isoformat()


class RunControl:
    """A pause request is not an observed wait, nor a durable checkpoint.

    Callers must enter ``wait`` without service/registry locks. Observers run
    outside the condition lock, so status persistence cannot lock out resume.
    ``paused`` means at least one boundary is waiting, not that parallel work
    already admitted before the request has been interrupted or completed.
    """

    def __init__(self, cancellation_event: Event, on_change=None):
        self.cancellation_event = cancellation_event
        self.pause_event = Event()
        self._condition = Condition()
        self._on_change = on_change
        self._requested_at = None
        self._paused_at = None
        self._waiters = {}
        self._closed = False

    def request_pause(self):
        with self._condition:
            if not self._closed and not self.cancellation_event.is_set():
                if not self.pause_event.is_set():
                    self._requested_at = _now()
                self.pause_event.set()
                if self._waiters and self._paused_at is None:
                    self._paused_at = _now()
            return self.snapshot()

    def resume(self):
        with self._condition:
            self.pause_event.clear()
            self._requested_at = self._paused_at = None
            self._condition.notify_all()
            return self.snapshot()

    def cancel(self):
        with self._condition:
            self.cancellation_event.set()
            return self.resume()

    def finish(self):
        with self._condition:
            self._closed = True
            self.resume()

    def snapshot(self):
        with self._condition:
            requested = self.pause_event.is_set() and not self.cancellation_event.is_set() and not self._closed
            paused = requested and bool(self._waiters)
            return {"pause_supported": True, "pause_requested": requested,
                    "pause_requested_at": self._requested_at if requested else None,
                    "paused": paused, "paused_at": self._paused_at if paused else None,
                    "pause_boundary": next(iter(self._waiters.values())) if paused else None,
                    "paused_boundary_count": len(self._waiters) if paused else 0}

    def _changed(self):
        if self._on_change is not None:
            self._on_change()

    def wait(self, boundary: str) -> bool:
        """Return False on cancellation/closure; otherwise admit the next call.

        The timed condition wait is only a fallback for existing callers that
        set the separate cancellation Event directly. HTTP cancel notifies
        immediately. No polling loop runs while the worker is unpaused.
        """
        token = object()
        with self._condition:
            if self.cancellation_event.is_set() or self._closed:
                return False
            if not self.pause_event.is_set():
                return True
            self._waiters[token] = boundary
            if self._paused_at is None:
                self._paused_at = _now()
        try:
            self._changed()
            with self._condition:
                while self.pause_event.is_set() and not self.cancellation_event.is_set() and not self._closed:
                    self._condition.wait(timeout=0.5)
                return not self.cancellation_event.is_set() and not self._closed
        finally:
            with self._condition:
                self._waiters.pop(token, None)
                if not self._waiters:
                    self._paused_at = None
            self._changed()