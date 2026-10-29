"""Test target selection: the emulator by default, the real heat pump on an explicit opt-in.

requirement-25: the plant read tests are one test set with two targets. The default target
is the emulator; it needs nothing and every gate runs on it. The live target is the real
heat pump, read-only, selected only when the owner sets ``GENESIS_WP_TEST_TARGET=live`` and
names the host in ``GENESIS_WP_LIVE_HOST`` (port in ``GENESIS_WP_LIVE_PORT``, default 502).

This module is the only place that reads those variables. The host is topology: no repr,
message or test id built here contains it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

TARGET_ENV = "GENESIS_WP_TEST_TARGET"
HOST_ENV = "GENESIS_WP_LIVE_HOST"
PORT_ENV = "GENESIS_WP_LIVE_PORT"
EMULATOR, LIVE = "emulator", "live"
DEFAULT_LIVE_PORT = 502


class TargetError(ValueError):
    """The live opt-in is set but incomplete or malformed."""


@dataclass(frozen=True)
class LiveTarget:
    """The real heat pump. ``repr`` hides the host."""

    host: str = field(repr=False)
    port: int = DEFAULT_LIVE_PORT

    def __str__(self) -> str:
        return LIVE


def selected(environ=os.environ) -> LiveTarget | None:
    """The live target if the owner opted in, else None (the emulator)."""
    target = environ.get(TARGET_ENV, EMULATOR).strip() or EMULATOR
    if target == EMULATOR:
        return None
    if target != LIVE:
        raise TargetError(f"{TARGET_ENV} must be {EMULATOR!r} or {LIVE!r}, got {target!r}")
    host = environ.get(HOST_ENV, "").strip()
    if not host:
        raise TargetError(f"{TARGET_ENV}={LIVE} needs {HOST_ENV}: the heat pump host, from the environment only")
    port_text = environ.get(PORT_ENV, "").strip() or str(DEFAULT_LIVE_PORT)
    if not port_text.isdigit() or not 0 < int(port_text) < 65536:
        raise TargetError(f"{PORT_ENV} must be a TCP port number")
    return LiveTarget(host, int(port_text))
