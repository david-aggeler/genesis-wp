"""Modbus TCP emulator for Genesis-platform heat pumps.

Built on pymodbus's own server (``ModbusTcpServer``) with the ``SimDevice``/``SimData``
datastore. The deprecated ``ModbusServerContext``/``ModbusDeviceContext`` classes are
avoided on purpose: in pymodbus 3.13.1 (the version Home Assistant 2026.10 ships) they
swap holding and input registers.

Two hooks carry the emulator's behavior:

- ``trace_pdu`` sees every incoming request before it is executed and may replace it. It
  records the request log, rejects unsupported function codes, injected faults and any
  request that touches an address outside the profile's ranges. Range checks are exact
  for all four blocks, which ``SimDevice`` alone does not do for coils/discrete inputs.
- the ``SimDevice`` action hook runs inside the datastore. It applies plant dynamics to
  the live registers and, when writes are not applied, turns writes into no-ops.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from pymodbus.constants import ExcCodes
from pymodbus.pdu import ExceptionResponse, ModbusPDU
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

from ..const import KEY_ADDRESS, KEY_NOT_FITTED, KEY_REG_TYPE, REG_INPUT, REGISTERS
from . import heating
from .capabilities import selection_overrides
from .climate import ClimateProfile
from .profile import (
    BLOCKS,
    COILS,
    DISCRETE_INPUTS,
    HOLDING_REGISTERS,
    INPUT_REGISTERS,
    DynamicRegister,
    Profile,
    ProfileError,
)

_LOGGER = logging.getLogger(__name__)

READ_FUNCTIONS = {1: COILS, 2: DISCRETE_INPUTS, 3: HOLDING_REGISTERS, 4: INPUT_REGISTERS}
WRITE_FUNCTIONS = {5: COILS, 6: HOLDING_REGISTERS, 15: COILS, 16: HOLDING_REGISTERS}
FUNCTION_BLOCKS = {**READ_FUNCTIONS, **WRITE_FUNCTIONS}


@dataclass(frozen=True)
class Request:
    """One request as received by the emulator."""

    function_code: int
    block: str | None
    address: int
    count: int
    values: tuple[int, ...] | None
    # None when the request went on to the datastore; otherwise the exception it got.
    rejected: ExcCodes | None
    timestamp: float

    @property
    def is_write(self) -> bool:
        return self.function_code in WRITE_FUNCTIONS


@dataclass
class Fault:
    """An injected failure: requests touching ``start..end`` of ``block`` get ``code``."""

    block: str
    start: int
    end: int
    code: ExcCodes = ExcCodes.DEVICE_FAILURE

    def hits(self, block: str, address: int, count: int) -> bool:
        return block == self.block and address <= self.end and address + count - 1 >= self.start


@dataclass
class _Drift:
    """State of one dynamic register (signed raw value)."""

    spec: DynamicRegister
    value: int


@dataclass
class Emulator:
    """One emulated heat pump on one TCP port.

    Use as an async context manager, or call :meth:`start` / :meth:`stop`::

        async with Emulator(get_profile("stiebel-wpe-i-07.1-plus-6.1.5"), port=0) as emu:
            ... connect to emu.host:emu.port ...
    """

    profile: Profile
    host: str = "127.0.0.1"
    port: int = 5020
    #: Simulate plant dynamics (drifting temperatures and compressor speed).
    dynamics: bool = False
    #: Seconds between dynamics steps while running; 0 = only on explicit step().
    dynamics_interval: float = 0.0
    seed: int = 0
    #: Apply writes to the register state. Writes are always recorded.
    apply_writes: bool = True
    #: Raw values by ``(block, address)`` served instead of the profile's (see Profile.with_overrides).
    #: Applied after ``capabilities``.
    overrides: dict[tuple[str, int], int] | None = None
    #: Plant capabilities switched on (True) or off (False), by library capability name (requirement-14).
    capabilities: Mapping[str, bool] | None = None
    #: Hold the outdoor temperature [°C] of the heating model instead of letting it drift.
    outdoor_temperature: float | None = None
    #: Start the HC2 room set point [°C] here instead of at the profile's initial value; applied as a change.
    initial_room_set_point_hc2: float | None = None
    #: Climate profile to play back as outdoor temperature on a simulated clock (needs dynamics).
    climate: ClimateProfile | None = None
    #: Simulated start date and time; default now, local, to the minute.
    start_time: datetime | None = None
    #: Simulated seconds per real second.
    playback_speed: float = 1.0

    requests: list[Request] = field(default_factory=list, init=False)
    faults: list[Fault] = field(default_factory=list, init=False)
    #: TCP connections accepted since start.
    connections: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.capabilities:
            values, notes = selection_overrides(self.profile, self.capabilities)
            profile = self.profile.with_overrides(values)
            source = {**profile.source, "notes": [*profile.source.get("notes", []), *notes]}
            self.profile = replace(profile, source=source)
        if self.overrides:
            self.profile = self.profile.with_overrides(self.overrides)
        self._rng = random.Random(self.seed)
        # A sensor served as not fitted stays so: dynamics never tick it (issue-12).
        not_fitted = self._not_fitted_inputs()
        self._drift = [
            _Drift(d, self._initial(d))
            for d in self.profile.dynamics
            if not (d.block == INPUT_REGISTERS and d.address in not_fitted)
        ] if self.dynamics else []
        # The heating model owns the curve points (room set points) whenever the profile has the
        # registers; with dynamics on it also owns the process inputs it computes.
        self.heating: heating.HeatingModel | None = None
        if self._has_heating_registers():
            self.heating = heating.HeatingModel.from_values(
                self.profile.blocks[HOLDING_REGISTERS].value,
                self.profile.blocks[INPUT_REGISTERS].value,
                outdoor=self.outdoor_temperature,
                coil_raw=self.profile.blocks[COILS].value,
                dinput_raw=self.profile.blocks[DISCRETE_INPUTS].value,
                not_fitted=not_fitted,
                rules=self.profile.room_set_points,
            )
        if self.initial_room_set_point_hc2 is not None:
            self.set_room_set_point_hc2(self.initial_room_set_point_hc2)
        if self.dynamics and self.heating is not None:
            owned = {(INPUT_REGISTERS, a) for a in heating.INPUTS}
            if not self.heating.running:
                # A stopped compressor stays stopped; the drift would put its speed back in range.
                owned.add((INPUT_REGISTERS, heating.SPEED))
            self._drift = [d for d in self._drift if (d.spec.block, d.spec.address) not in owned]
        # Simulated clock (requirement-22): only with a climate profile, which needs the heating model.
        self._clock: datetime | None = None
        self._carry = 0.0  # simulated seconds not yet run as a whole step
        if self.climate is not None:
            if not self.dynamics or self.heating is None:
                raise ValueError("a climate profile needs dynamics and a profile with the heating registers")
            self._check_speed(self.playback_speed)
            self._clock = self.start_time or datetime.now().replace(second=0, microsecond=0)
            self.heating.walk_outdoor = False
            self._feed_outdoor()
        self._server: ModbusTcpServer | None = None
        self._ticker: asyncio.Task | None = None

    # ---- public API -------------------------------------------------------------

    @property
    def writes(self) -> list[Request]:
        """Every write request received (function codes 5, 6, 15, 16), accepted or not."""
        return [r for r in self.requests if r.is_write]

    def inject_fault(self, block: str, start: int, end: int | None = None, code: ExcCodes = ExcCodes.DEVICE_FAILURE) -> Fault:
        """Make requests touching ``block[start..end]`` fail with Modbus exception ``code``."""
        if block not in BLOCKS:
            raise ValueError(f"unknown block {block!r}")
        fault = Fault(block, start, start if end is None else end, code)
        self.faults.append(fault)
        return fault

    def clear_faults(self) -> None:
        self.faults.clear()

    @property
    def room_set_point_hc2(self) -> float | None:
        """HC2 room set point [°C]; None when the profile has no rule for it."""
        point = self.heating.room_set_points.get("hc2") if self.heating else None
        return point.value if point else None

    def set_room_set_point_hc2(self, celsius: float) -> None:
        """Change the HC2 room set point: emulator only, the WPE-I has no register for it (formal_review-3).

        Shifts holding 109-115 by the profile's rule; visible on the next read.
        """
        if self.heating is None or "hc2" not in self.heating.room_set_points:
            raise ProfileError(f"profile {self.profile.name} has no room_set_points rule for hc2")
        shift = self.heating.set_room_set_point("hc2", float(celsius))
        _LOGGER.info("HC2 room set point %.1f °C, curve shift %+d K", celsius, shift)

    @property
    def simulated_time(self) -> datetime | None:
        """Simulated date and time; None without a climate profile."""
        return self._clock

    def set_playback_speed(self, speed: float) -> None:
        """Simulated seconds per real second; takes effect on the next tick."""
        self._check_speed(speed)
        self.playback_speed = float(speed)
        _LOGGER.info("playback speed %g", speed)

    def play(self, real_seconds: float) -> None:
        """Advance the simulated clock by ``real_seconds`` at the playback speed."""
        self.advance(real_seconds * self.playback_speed)

    def advance(self, simulated_seconds: float) -> None:
        """Advance the simulated clock, one model step per whole simulated minute; the rest carries over."""
        if self._clock is None:
            raise RuntimeError("no simulated clock: load a climate profile")
        self._carry += simulated_seconds
        steps = int(self._carry // heating.STEP_SECONDS)
        self._carry -= steps * heating.STEP_SECONDS
        if steps > 10_000:
            _LOGGER.warning("one advance runs %d steps; lower the playback speed or the tick interval", steps)
        for _ in range(steps):
            self._clock += timedelta(seconds=heating.STEP_SECONDS)
            self._feed_outdoor()
            self.step()

    def _feed_outdoor(self) -> None:
        if not self.heating.hold_outdoor:
            self.heating.outdoor = self.climate.value_at(self._clock)

    @staticmethod
    def _check_speed(speed: float) -> None:
        if not speed > 0:
            raise ValueError(f"playback speed must be > 0, got {speed}")

    def step(self, steps: int = 1) -> None:
        """Advance plant dynamics by ``steps``. No-op when dynamics are off."""
        for _ in range(steps):
            for drift in self._drift:
                spec = drift.spec
                delta = self._rng.randint(1, spec.max_step) * self._rng.choice((-1, 1))
                new = drift.value + delta
                if not spec.min <= new <= spec.max:
                    new = drift.value - delta  # reflect at the bound
                drift.value = min(max(new, spec.min), spec.max)
            if self.dynamics and self.heating is not None:
                self.heating.observe({
                    d.spec.address: d.value for d in self._drift
                    if d.spec.block == INPUT_REGISTERS and d.spec.address in heating.COMPRESSOR
                })
                self.heating.step(self._rng)

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("emulator already running")
        if self.port == 0:
            self.port = _free_port(self.host)
        self._server = ModbusTcpServer(
            self._build_device(),
            address=(self.host, self.port),
            trace_pdu=self._trace_pdu,
            trace_connect=self._trace_connect,
        )
        await self._server.serve_forever(background=True)
        if self.dynamics and self.dynamics_interval > 0:
            self._ticker = asyncio.create_task(self._tick())
        _LOGGER.info("emulator %s listening on %s:%s", self.profile.name, self.host, self.port)

    async def stop(self) -> None:
        if self._ticker:
            self._ticker.cancel()
            self._ticker = None
        if self._server:
            await self._server.shutdown()
            self._server = None

    async def __aenter__(self) -> Emulator:
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    # ---- datastore ----------------------------------------------------------------

    def _build_device(self) -> SimDevice:
        lists = []
        for name in BLOCKS:
            block = self.profile.blocks[name]
            entries = []
            for start, end in block.ranges:
                raw = [block.value(a) for a in range(start, end + 1)]
                if block.is_bits:
                    entries.append(SimData(start, values=[bool(v) for v in raw], datatype=DataType.BITS))
                else:
                    # DataType.REGISTERS packs as signed int16; pass raw words in that form.
                    signed = [v - 0x10000 if v >= 0x8000 else v for v in raw]
                    entries.append(SimData(start, values=signed, datatype=DataType.REGISTERS))
            lists.append(entries)
        # SimDevice tuple order: coils, discrete inputs, holding registers, input registers.
        return SimDevice(id=self.profile.unit_id, simdata=tuple(lists), action=self._action)

    async def _action(self, function_code, start_address, address, count, registers, values):
        """SimDevice hook: runs inside the datastore with the block's live registers."""
        block = FUNCTION_BLOCKS.get(function_code)
        for drift in self._drift:
            if drift.spec.block == block:
                registers[drift.spec.address - start_address] = drift.value & 0xFFFF
        if self.heating is not None:
            if block == INPUT_REGISTERS and self.dynamics:
                for address, raw in self.heating.inputs().items():
                    registers[address - start_address] = raw
            elif block == HOLDING_REGISTERS:
                if values is not None and self.apply_writes:
                    for offset, raw in enumerate(values):
                        self.heating.write(address + offset, raw & 0xFFFF)
                # After the write, so a room set point write shows its curve shift at once.
                for owned, raw in self.heating.holding_registers().items():
                    registers[owned - start_address] = raw
            elif block == COILS and values is not None and self.apply_writes:
                for offset, bit in enumerate(values):
                    self.heating.write_coil(address + offset, bool(bit))
            elif block == DISCRETE_INPUTS and self.dynamics:
                for bit_address, on in self.heating.discrete_inputs().items():
                    index = bit_address - start_address * 16
                    word, bit = divmod(index, 16)
                    if 0 <= word < len(registers):
                        registers[word] = registers[word] | (1 << bit) if on else registers[word] & ~(1 << bit)
        if values is not None and not self.apply_writes:
            # Keep the current state: overwrite the incoming values with what is stored.
            if block in (COILS, DISCRETE_INPUTS):
                bits = _registers_to_bits(registers)
                first = address - start_address * 16
                values[:] = bits[first : first + len(values)]
            else:
                first = address - start_address
                values[:] = registers[first : first + len(values)]
        return None

    def _has_heating_registers(self) -> bool:
        holding, inputs = self.profile.blocks[HOLDING_REGISTERS], self.profile.blocks[INPUT_REGISTERS]
        return (
            all(holding.implemented(a) for a in (*heating.HOLDING, heating.MODE_HC2, *heating.COOLING_HOLDING))
            and all(inputs.implemented(a) for a in (*heating.INPUTS, *heating.COMPRESSOR))
            and self.profile.blocks[COILS].implemented(heating.ENABLE_PASSIVE_COOLING)
            and self.profile.blocks[DISCRETE_INPUTS].implemented(heating.PASSIVE_COOLING_ACTIVE)
        )

    def _not_fitted_inputs(self) -> frozenset[int]:
        """Input addresses whose served raw value is the model's not-fitted sentinel."""
        inputs = self.profile.blocks[INPUT_REGISTERS]
        addresses = set()
        for meta in REGISTERS.values():
            sentinel = meta.get(KEY_NOT_FITTED, {}).get(self.profile.model)
            if meta[KEY_REG_TYPE] == REG_INPUT and sentinel is not None and inputs.implemented(meta[KEY_ADDRESS]):
                if inputs.value(meta[KEY_ADDRESS]) == sentinel:
                    addresses.add(meta[KEY_ADDRESS])
        return frozenset(addresses)

    def _initial(self, spec: DynamicRegister) -> int:
        raw = self.profile.blocks[spec.block].value(spec.address)
        return raw - 0x10000 if spec.signed and raw >= 0x8000 else raw

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(self.dynamics_interval)
            if self._clock is not None:
                self.play(self.dynamics_interval)
            else:
                self.step()

    def _trace_connect(self, connecting: bool) -> None:
        if connecting:
            self.connections += 1

    # ---- request gate -------------------------------------------------------------

    def _trace_pdu(self, sending: bool, pdu: ModbusPDU) -> ModbusPDU:
        if sending:
            return pdu
        fc = pdu.function_code
        block = FUNCTION_BLOCKS.get(fc)
        if fc in (5, 15):
            values, count = tuple(int(b) for b in pdu.bits), len(pdu.bits)
        elif fc in (6, 16):
            values, count = tuple(pdu.registers), len(pdu.registers)
        else:
            values, count = None, pdu.count
        code = self._check(block, pdu.address, count)
        self.requests.append(Request(fc, block, pdu.address, count, values, code, time.time()))
        _LOGGER.debug("fc=%s %s %s+%s values=%s -> %s", fc, block, pdu.address, count, values, code or "ok")
        if code is not None:
            pdu.datastore_update = _rejection(fc, code)  # replaces this request's execution
        return pdu

    def _check(self, block: str | None, address: int, count: int) -> ExcCodes | None:
        if block is None:
            return ExcCodes.ILLEGAL_FUNCTION
        for fault in self.faults:
            if fault.hits(block, address, count):
                return fault.code
        if not self.profile.blocks[block].covers(address, count):
            return ExcCodes.ILLEGAL_ADDRESS
        return None


def _rejection(function_code: int, code: ExcCodes):
    async def datastore_update(context, device_id) -> ModbusPDU:  # noqa: ARG001
        return ExceptionResponse(function_code, code)

    return datastore_update


def _registers_to_bits(registers: list[int]) -> list[bool]:
    """Unpack registers into bits, bit 0 of register 0 first (pymodbus SimUtils order)."""
    return [bool(reg >> i & 1) for reg in registers for i in range(16)]


def _free_port(host: str) -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]
