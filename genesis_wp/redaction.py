"""Keep the device host out of the transport's log records (requirement-9, issue-16).

pymodbus formats the address it connects to into its own log messages. Every pymodbus
record goes through one logger, ``pymodbus.logging``, so one filter on that logger sees
each record at creation, before any handler. The filter replaces the registered names with
``<device>``; it never drops a record, so the log still reports what happened.

The library registers its own host while it owns a connection. A caller that injects its
own ``ModbusUnit`` registers that connection's host with :func:`redact_transport_host`.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter

PLACEHOLDER = "<device>"
TRANSPORT_LOGGER = "pymodbus.logging"

_FORMATTER = logging.Formatter()


class _HostFilter(logging.Filter):
    """Replaces every registered name in a record's text; names are reference-counted."""

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._names: Counter[str] = Counter()
        self._ordered: tuple[str, ...] = ()  # longest first, so a name inside another goes last

    def add(self, name: str) -> None:
        with self._lock:
            self._names[name] += 1
            self._reorder()

    def discard(self, name: str) -> None:
        with self._lock:
            self._names[name] -= 1
            if self._names[name] <= 0:
                del self._names[name]
            self._reorder()

    def _reorder(self) -> None:
        self._ordered = tuple(sorted(self._names, key=len, reverse=True))

    def filter(self, record: logging.LogRecord) -> bool:
        names = self._ordered
        if not names:
            return True
        text = record.getMessage()
        if record.exc_info:
            text += "\n" + _FORMATTER.formatException(record.exc_info)
        if any(name in text for name in names):
            for name in names:
                text = text.replace(name, PLACEHOLDER)
            record.msg, record.args = text, ()
            record.exc_info = record.exc_text = None  # folded into msg above
        return True


_FILTER = _HostFilter()
logging.getLogger(TRANSPORT_LOGGER).addFilter(_FILTER)


class RedactionHandle:
    """The names one holder registered; :meth:`remove` releases them, once."""

    def __init__(self):
        self._names: list[str] = []
        self._removed = False

    def add(self, *names: str) -> None:
        """Register more names, for example the addresses the host resolved to."""
        if self._removed:
            raise RuntimeError("redaction handle already removed")
        for name in names:
            if name and name not in self._names:
                self._names.append(name)
                _FILTER.add(name)

    def remove(self) -> None:
        if self._removed:
            return
        self._removed = True
        for name in self._names:
            _FILTER.discard(name)
        self._names.clear()


def redact_transport_host(*names: str) -> RedactionHandle:
    """Replace ``names`` (host name, addresses) in transport log records until ``remove()``."""
    handle = RedactionHandle()
    handle.add(*names)
    return handle
