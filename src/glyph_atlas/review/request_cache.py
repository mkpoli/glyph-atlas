"""Share repeated filesystem lookups within one HTTP request only."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

_lookups: ContextVar[dict | None] = ContextVar("review_request_lookups", default=None)


@contextmanager
def lookup_scope():
    token = _lookups.set({})
    try:
        yield
    finally:
        _lookups.reset(token)


def memoize(function):
    @wraps(function)
    def cached(*args, **kwargs):
        lookups = _lookups.get()
        if lookups is None:
            return function(*args, **kwargs)
        key = (function, args, tuple(sorted(kwargs.items())))
        if key not in lookups:
            lookups[key] = function(*args, **kwargs)
        return lookups[key]
    return cached


@memoize
def file_stamp(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None
