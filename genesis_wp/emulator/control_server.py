"""Emulator control channel: the server side (requirement-24).

A thin dispatcher: validates each request strictly, calls the ``Emulator`` method and
answers. The emulator stays the single owner of its state. Protocol constants live in
``control``; see there for the wire format.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from . import control as c

if TYPE_CHECKING:
    from .server import Emulator

_LOGGER = logging.getLogger(__name__)


class _Reject(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _package_version() -> str:
    try:
        return importlib.metadata.version("genesis-wp")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _args(args: dict, *names: str) -> list[Any]:
    """The named arguments, all required, no others."""
    extra = sorted(set(args) - set(names))
    missing = [name for name in names if name not in args]
    if extra or missing:
        raise _Reject(c.INVALID_VALUE, f"expected arguments {list(names)}, missing {missing}, unexpected {extra}")
    return [args[name] for name in names]


class ControlServer:
    """Serves one emulator's control channel on ``host:port`` (0 = a free port)."""

    def __init__(self, emulator: Emulator, host: str, port: int):
        self._emu = emulator
        self._host = host
        self._port = port
        self._server: asyncio.Server | None = None
        self._ops: dict[str, Callable[[dict], Any]] = {
            c.IDENTIFY: self._identify,
            c.STATE: self._state,
            c.SET_PLAYBACK_SPEED: self._set_playback_speed,
            c.PAUSE: self._pause,
            c.RESUME: self._resume,
            c.SET_SIMULATED_TIME: self._set_simulated_time,
            c.HOLD_OUTDOOR: self._hold_outdoor,
            c.RELEASE_OUTDOOR: self._release_outdoor,
        }
        assert tuple(self._ops) == c.OPERATIONS

    @property
    def port(self) -> int:
        """The bound port; after start() also when 0 was asked for."""
        return self._port

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self._host, self._port, limit=c.MAX_LINE)
        self._port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                try:
                    line = await reader.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    writer.write(self._encode(self._error(c.BAD_REQUEST, f"request longer than {c.MAX_LINE} bytes")))
                    break
                if not line:
                    break
                writer.write(self._encode(self._answer(line)))
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()

    def _answer(self, line: bytes) -> dict:
        try:
            request = json.loads(line)
        except ValueError:
            return self._error(c.BAD_REQUEST, "not a JSON line")
        if not isinstance(request, dict) or not isinstance(request.get("op"), str):
            return self._error(c.BAD_REQUEST, "a request is an object with a string 'op'")
        args = request.get("args", {})
        if not isinstance(args, dict):
            return self._error(c.BAD_REQUEST, "'args' must be an object")
        handler = self._ops.get(request["op"])
        if handler is None:
            return self._error(c.UNKNOWN_OPERATION, f"no operation {request['op']!r}; known: {list(c.OPERATIONS)}")
        try:
            result = handler(args)
        except _Reject as reject:
            return self._error(reject.code, reject.message)
        _LOGGER.info("control: %s %s", request["op"], args or "")
        return {"ok": True, "result": result}

    @staticmethod
    def _error(code: str, message: str) -> dict:
        return {"ok": False, "error": {"code": code, "message": message}}

    @staticmethod
    def _encode(reply: dict) -> bytes:
        return json.dumps(reply, allow_nan=False).encode() + b"\n"

    # ---- operations -------------------------------------------------------------------

    def _need_clock(self) -> None:
        if self._emu.simulated_time is None:
            raise _Reject(c.UNAVAILABLE, "the emulator runs without a simulated clock (no climate profile)")

    def _need_dynamics(self) -> None:
        if not self._emu.dynamics or self._emu.heating is None:
            raise _Reject(c.UNAVAILABLE, "the emulator runs without dynamics")

    def _identify(self, args: dict) -> dict:
        _args(args)
        return {
            "emulator": c.EMULATOR_NAME,
            "protocol": c.PROTOCOL,
            "version": _package_version(),
            "profile": self._emu.profile.name,
            "firmware": dict(self._emu.profile.firmware),
        }

    def _state(self, args: dict) -> dict:
        _args(args)
        emu, model, climate = self._emu, self._emu.heating, self._emu.climate
        clock = emu.simulated_time
        return {
            "profile": emu.profile.name,
            "dynamics": emu.dynamics,
            "simulated_time": clock.isoformat() if clock else None,
            "playback_speed": emu.playback_speed if clock else None,
            "paused": emu.paused,
            "outdoor_temperature": round(model.outdoor, 2) if model and emu.dynamics else None,
            "outdoor_held": bool(model and model.hold_outdoor),
            "climate": {
                "bucket": climate.bucket, "start": climate.start.isoformat(), "end": climate.end.isoformat(),
                "source": climate.source, "license": climate.license,
            } if climate else None,
        }

    def _set_playback_speed(self, args: dict) -> None:
        (speed,) = _args(args, "speed")
        self._need_clock()
        if not c.is_number(speed) or not speed > 0:
            raise _Reject(c.INVALID_VALUE, f"speed must be a finite number > 0, got {speed!r}")
        self._emu.set_playback_speed(speed)

    def _pause(self, args: dict) -> None:
        _args(args)
        self._need_clock()
        self._emu.pause()

    def _resume(self, args: dict) -> None:
        _args(args)
        self._need_clock()
        self._emu.resume()

    def _set_simulated_time(self, args: dict) -> None:
        (text,) = _args(args, "time")
        self._need_clock()
        try:
            when = datetime.fromisoformat(text) if isinstance(text, str) else None
        except ValueError:
            when = None
        if when is None or when.tzinfo is not None:
            raise _Reject(c.INVALID_VALUE, f"time must be an ISO 8601 date and time without an offset, got {text!r}")
        self._emu.set_simulated_time(when)

    def _hold_outdoor(self, args: dict) -> None:
        (celsius,) = _args(args, "celsius")
        self._need_dynamics()
        low, high = c.OUTDOOR_RANGE
        if not c.is_number(celsius) or not low <= celsius <= high:
            raise _Reject(c.INVALID_VALUE, f"celsius must be a number in [{low}, {high}], got {celsius!r}")
        self._emu.hold_outdoor(celsius)

    def _release_outdoor(self, args: dict) -> None:
        _args(args)
        self._need_dynamics()
        self._emu.release_outdoor()
