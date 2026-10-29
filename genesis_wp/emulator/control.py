"""Emulator control channel: protocol and asyncio client (requirement-24).

The emulator's Modbus surface matches the real plant's; emulator-only controls (playback
speed, pause, simulated time, held outdoor temperature) travel over this separate channel on
its own TCP port. Plant values, including set points the plant has registers for, stay on
Modbus. There is no operation for a value the real unit has no register for.

Wire format, protocol version 1: one JSON object per line, UTF-8. A request is
``{"op": <name>, "args": {...}}``; a reply is ``{"ok": true, "result": ...}`` or
``{"ok": false, "error": {"code": <code>, "message": <text>}}``. Datetimes are ISO 8601
without an offset: the simulated clock has no time zone.

``identify()`` is the guard against a wrong host: a real heat pump has no control endpoint,
so the client raises ``NotAnEmulator`` on a refused connection, a timeout, a closed
connection, a reply that is not this protocol, or a different emulator name. The request
line starts with ``{"op"``; read as a Modbus TCP frame, its function code byte is ``"``
(0x22), never a write.

Stdlib only: this module imports neither pymodbus nor Home Assistant, so consumers and
tests can use it without the emulator extra. Error messages never contain the host.
"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

EMULATOR_NAME = "genesis-wp-emulator"
PROTOCOL = 1
MAX_LINE = 64 * 1024

# Operations
IDENTIFY = "identify"
STATE = "state"
SET_PLAYBACK_SPEED = "set_playback_speed"
PAUSE = "pause"
RESUME = "resume"
SET_SIMULATED_TIME = "set_simulated_time"
HOLD_OUTDOOR = "hold_outdoor"
RELEASE_OUTDOOR = "release_outdoor"
OPERATIONS = (IDENTIFY, STATE, SET_PLAYBACK_SPEED, PAUSE, RESUME, SET_SIMULATED_TIME, HOLD_OUTDOOR, RELEASE_OUTDOOR)

# Error codes
BAD_REQUEST = "bad_request"  # not a JSON object with a string op and an args object
UNKNOWN_OPERATION = "unknown_operation"
INVALID_VALUE = "invalid_value"  # missing, extra, wrongly typed or out-of-range argument
UNAVAILABLE = "unavailable"  # the emulator runs without the feature (no clock, no dynamics)
NOT_AN_EMULATOR = "not_an_emulator"

#: Range a held outdoor temperature must lie in [°C].
OUTDOOR_RANGE = (-50.0, 60.0)


class ControlError(Exception):
    """The emulator rejected a request, or the channel failed."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class NotAnEmulator(ControlError):
    """The endpoint is not a genesis-wp emulator control channel."""

    def __init__(self, message: str):
        super().__init__(NOT_AN_EMULATOR, message)


@dataclass(frozen=True)
class Identity:
    emulator: str
    protocol: int
    version: str
    profile: str
    firmware: dict


@dataclass(frozen=True)
class State:
    profile: str
    dynamics: bool
    simulated_time: datetime | None
    playback_speed: float | None  # None without a simulated clock
    paused: bool
    outdoor_temperature: float | None  # °C; None without the heating model
    outdoor_held: bool
    climate: dict | None  # bucket, period and source of the loaded climate profile


def is_number(value: Any) -> bool:
    """A finite JSON number; booleans are not numbers here."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class EmulatorControl:
    """Asyncio client for one emulator's control channel.

    Use as an async context manager or call :meth:`close`; the connection opens on the first
    call. Every call is bounded by ``timeout`` seconds.
    """

    def __init__(self, host: str, port: int, *, timeout: float = 2.0):
        self._host = host
        self._port = port
        self._timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return "EmulatorControl()"

    async def __aenter__(self) -> EmulatorControl:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        writer, self._reader, self._writer = self._writer, None, None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    # ---- operations -------------------------------------------------------------------

    async def identify(self) -> Identity:
        result = await self._call(IDENTIFY)
        try:
            identity = Identity(
                str(result["emulator"]), int(result["protocol"]), str(result["version"]),
                str(result["profile"]), dict(result["firmware"]),
            )
        except (KeyError, TypeError, ValueError):
            raise NotAnEmulator("identify reply is not this protocol") from None
        if identity.emulator != EMULATOR_NAME:
            raise NotAnEmulator(f"endpoint identifies as {identity.emulator!r}")
        if identity.protocol != PROTOCOL:
            raise NotAnEmulator(f"control protocol {identity.protocol}, this client speaks {PROTOCOL}")
        return identity

    async def state(self) -> State:
        r = await self._call(STATE)
        when = r.get("simulated_time")
        return State(
            r["profile"], r["dynamics"], datetime.fromisoformat(when) if when else None, r["playback_speed"],
            r["paused"], r["outdoor_temperature"], r["outdoor_held"], r["climate"],
        )

    async def set_playback_speed(self, speed: float) -> None:
        """Simulated seconds per real second, > 0."""
        await self._call(SET_PLAYBACK_SPEED, speed=speed)

    async def pause(self) -> None:
        await self._call(PAUSE)

    async def resume(self) -> None:
        await self._call(RESUME)

    async def set_simulated_time(self, when: datetime) -> None:
        """Jump the simulated clock; a naive datetime, as the clock has no time zone."""
        await self._call(SET_SIMULATED_TIME, time=when.isoformat() if isinstance(when, datetime) else when)

    async def hold_outdoor(self, celsius: float) -> None:
        """Hold the outdoor temperature (input 13) instead of the climate profile or the drift."""
        await self._call(HOLD_OUTDOOR, celsius=celsius)

    async def release_outdoor(self) -> None:
        await self._call(RELEASE_OUTDOOR)

    # ---- transport --------------------------------------------------------------------

    async def _call(self, op: str, **args: Any) -> Any:
        # allow_nan: an invalid value reaches the server, which rejects it; nothing is coerced here.
        line = json.dumps({"op": op, "args": args}, allow_nan=True).encode() + b"\n"
        async with self._lock:
            try:
                return await asyncio.wait_for(self._exchange(line), self._timeout)
            except NotAnEmulator:
                await self.close()
                raise
            except ControlError:
                raise
            except (TimeoutError, asyncio.TimeoutError):
                await self.close()
                raise NotAnEmulator("no control reply within the timeout") from None
            except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError) as err:
                await self.close()
                raise NotAnEmulator(f"control channel failed ({type(err).__name__})") from None

    async def _exchange(self, line: bytes) -> Any:
        if self._writer is None:
            self._reader, self._writer = await asyncio.open_connection(self._host, self._port, limit=MAX_LINE)
        self._writer.write(line)
        await self._writer.drain()
        raw = await self._reader.readline()
        if not raw.endswith(b"\n"):
            raise NotAnEmulator("connection closed without a control reply")
        reply = json.loads(raw)
        if not isinstance(reply, dict) or not isinstance(reply.get("ok"), bool):
            raise NotAnEmulator("reply is not this protocol")
        if reply["ok"]:
            return reply.get("result")
        error = reply.get("error")
        if not isinstance(error, dict):
            raise NotAnEmulator("reply is not this protocol")
        raise ControlError(str(error.get("code")), str(error.get("message")))
