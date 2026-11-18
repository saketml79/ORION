"""Explicit XML intake budgets, independent of hosted metadata budgets."""
import os

GIB = 1024 ** 3


def _bounded_env(name, default, maximum):
    value = os.environ.get(name, str(default))
    if not value.isascii() or not value.isdecimal():
        raise ValueError(f"{name} must be a positive integer byte count")
    result = int(value)
    if not 1 <= result <= maximum:
        raise ValueError(f"{name} exceeds explicit intake limits")
    return result


def max_xml_bytes():
    return _bounded_env("ORION_MAX_XML_BYTES", 8 * GIB, 32 * GIB)


def max_batch_bytes():
    result = _bounded_env("ORION_MAX_BATCH_BYTES", 32 * GIB, 32 * GIB)
    if result < max_xml_bytes():
        raise ValueError("ORION_MAX_BATCH_BYTES must be at least ORION_MAX_XML_BYTES")
    return result