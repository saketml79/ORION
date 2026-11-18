"""Serialize Delta append/commit writes within the single hosted app process.

Reads remain concurrent. This lock only coordinates write transactions that
share the user's Delta tables while this one-replica app is running.
"""
from __future__ import annotations

from threading import RLock
from typing import Any, Callable

DELTA_WRITE_LOCK = RLock()


def serialize_delta_write(function: Callable[..., Any], *args, **kwargs):
    with DELTA_WRITE_LOCK:
        return function(*args, **kwargs)
