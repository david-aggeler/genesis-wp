"""Plant read checks: invariants that hold on any healthy WPE-I, emulated or real (requirement-25).

A plant read test is trusted once it passes against the real heat pump; the emulator is
correct when it passes the same test. So nothing here compares against captured values.
Each check takes one ``Snapshot`` (one full library update) and returns its violations.

The live target is read through ``ReadOnlyUnit``: the library client is built read-only, and
the unit itself has no write methods, so a write attempt fails before a connection is made.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from modbus_connection import ModbusConnectionError, ModbusTcpParams, ModbusTimeoutError

from genesis_wp import EnumValue, ThermiaGenesis
from genesis_wp.const import (
    KEY_DATATYPE,
    KEY_NOT_FITTED,
    KEY_SCALE,
    REGISTERS,
    TYPE_BIT,
    TYPE_STATUS,
)
from genesis_wp.emulator.profile import get_profile, packaged_profiles

from .harness import WPE_I_KIND

#: Modbus unit methods that write (FC 5, 6, 15, 16 and the combined forms). Never on a plant target.
WRITE_METHODS = frozenset({
    "write_coil", "write_coils", "write_register", "write_registers",
    "mask_write_register", "read_write_registers", "write_file_record",
})
READ_METHODS = ("read_coils", "read_discrete_inputs", "read_input_registers", "read_holding_registers")


class ReadOnlyTransportError(PermissionError):
    """A write was attempted through the plant test transport."""


class PlantConnectionError(ConnectionError):
    """The plant target could not be reached. The message never names the host."""


class ReadOnlyUnit:
    """A modbus-connection unit that can only read; it connects on the first read.

    Second line of defense under the library's own read-only refusal (requirement-1): even a
    client built with writes allowed could not write through this unit.
    """

    def __init__(self, host: str, port: int, unit_id: int = 1, timeout: float = 5.0):
        self._params = ModbusTcpParams(host=host, port=port)
        self._unit_id = unit_id
        self._timeout = timeout
        self._connection = None
        self._unit = None

    def __repr__(self) -> str:
        return f"ReadOnlyUnit(unit={self._unit_id})"

    def __getattr__(self, name: str):
        if name in WRITE_METHODS:
            raise ReadOnlyTransportError(f"{name}: the plant test transport is read-only")
        raise AttributeError(name)

    async def _connected(self):
        if self._connection is None:
            from modbus_connection.pymodbus import ModbusConnection

            self._connection = ModbusConnection(self._params, timeout=self._timeout)
            self._unit = self._connection.for_unit(self._unit_id)
        if not self._connection.connected:
            failure = None
            try:
                await self._connection.connect()
            except (ModbusConnectionError, ModbusTimeoutError, OSError) as err:
                failure = type(err).__name__
            if failure is not None:  # raised outside the except block: no chained text with the host
                raise PlantConnectionError(f"cannot connect to the plant target ({failure})")
        return self._unit

    async def read_coils(self, address, count):
        return await (await self._connected()).read_coils(address, count)

    async def read_discrete_inputs(self, address, count):
        return await (await self._connected()).read_discrete_inputs(address, count)

    async def read_input_registers(self, address, count):
        return await (await self._connected()).read_input_registers(address, count)

    async def read_holding_registers(self, address, count):
        return await (await self._connected()).read_holding_registers(address, count)

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None


@dataclass(frozen=True)
class Snapshot:
    """One full library update of one target."""

    target: str  # "live" or the emulator profile name; never a host
    data: dict = field(repr=False)
    labels: dict[str, EnumValue] = field(repr=False)
    firmware: str | None


async def read_snapshot(target: str, host: str, port: int, *, delay: float = 0.1) -> Snapshot:
    """Run one full WPE-I update through a read-only unit."""
    unit = ReadOnlyUnit(host, port)
    client = ThermiaGenesis(kind=WPE_I_KIND, unit=unit, delay=delay, max_registers=16)
    try:
        data = await client.async_update()
    finally:
        await unit.close()
    return Snapshot(target, data, client.labels, client.firmware)


# --- invariants -------------------------------------------------------------------------

#: Read only when coil 41 (fixed system supply set point) is on: the library skips it otherwise.
CONDITIONAL = {"holding_fixed_system_supply_set_point": "coil_enable_fixed_system_supply_set_point"}

#: Plausible range of a measured or set temperature on a brine heat pump [°C]. Not a documented
#: register range (the register map has none); a sanity bound a healthy plant stays inside.
PLAUSIBLE_TEMPERATURE = (-50.0, 100.0)
#: Registers that read implausible values for a known, recorded reason.
KNOWN_IMPLAUSIBLE = {
    "input_hgw_supply_line_temperature": "issue-15",
    "holding_outdoor_temperature_sensor": "issue-15",
}

#: Outdoor temperatures [°C] of the seven heat curve points, inputs 20-26 (formal_review-3).
CURVE_X = (17.0, 10.0, 5.0, 0.0, -5.0, -15.0, -25.0)
#: Tolerance between a computed and the reported supply set point [K]: two decimal places on
#: the plant, plus the controller reading outdoor between our reads.
SET_POINT_TOLERANCE = 0.5

HC1 = {
    "set_point": "input_system_supply_line_calculated_set_point",
    "curve": tuple(f"holding_set_point_heat_curve_y_{i}" for i in range(1, 8)),
    "min": "holding_min_limitation",
    "max": "holding_max_limitation",
}
HC2 = {
    "set_point": "input_desired_temperature_distribution_circuit_mix_valve_1",
    "curve": tuple(f"holding_set_point_curve_y_coordinate_{i}_mix_valve_1" for i in range(1, 8)),
    "min": "holding_min_limitation_set_point_curve_radiator_mix_valve_1",
    "max": "holding_max_limitation_set_point_curve_radiator_mix_valve_1",
    "enabled": "coil_enable_mix_valve_1",
    "cooling": "dinput_indication_when_mixing_valve_1_is_producing_passive_cooling",
}
OUTDOOR = "input_outdoor_temperature"


def _enabled_registers() -> list[str]:
    return [name for name, meta in REGISTERS.items() if meta.get(WPE_I_KIND)]


def firmware_is_a_captured_profile(s: Snapshot) -> list[str]:
    """The firmware is one we have a profile for; otherwise a new read-only probe is due."""
    known = {get_profile(name).firmware["software"] for name in packaged_profiles()}
    return [] if s.firmware in known else [f"firmware {s.firmware!r} has no profile (known: {sorted(known)})"]


def every_register_reads(s: Snapshot) -> list[str]:
    """Every register the model enables is in the update, or skipped for its documented reason."""
    missing = []
    for name in _enabled_registers():
        if name in s.data:
            continue
        gate = CONDITIONAL.get(name)
        if gate is not None and s.data.get(gate) is False:
            continue
        missing.append(name)
    return [f"not read: {name}" for name in missing]


def values_have_their_declared_type(s: Snapshot) -> list[str]:
    """Bits are booleans, status registers labels, numbers numbers; None only for a declared sentinel."""
    wrong = (_type_error(name, value) for name, value in s.data.items())
    return [error for error in wrong if error]


def _type_error(name: str, value) -> str | None:
    meta = REGISTERS[name]
    kind = meta[KEY_DATATYPE]
    if value is None:
        declared = WPE_I_KIND in meta.get(KEY_NOT_FITTED, {})
        return None if declared else f"{name}: None without a declared not-fitted sentinel"
    if kind == TYPE_BIT:
        return None if isinstance(value, bool) else f"{name}: {value!r} is not a boolean"
    if kind == TYPE_STATUS:
        return None if isinstance(value, str) else f"{name}: {value!r} is not a status label"
    finite = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    return None if finite else f"{name}: {value!r} is not a finite number"


def temperatures_are_plausible(s: Snapshot) -> list[str]:
    """Every temperature at scale 100 lies in the plausible range."""
    low, high = PLAUSIBLE_TEMPERATURE
    return [
        f"{name}: {value} °C outside [{low}, {high}]"
        for name, value in s.data.items()
        if "temperature" in name and REGISTERS[name][KEY_SCALE] == 100 and value is not None
        and name not in KNOWN_IMPLAUSIBLE and not low <= value <= high
    ]


def enum_values_are_documented(s: Snapshot) -> list[str]:
    """Every enumerated register reads a value its source documents a label for."""
    return [f"{name}: raw {enum.value} has no documented label" for name, enum in s.labels.items() if not enum.labeled]


def heat_curve_x_coordinates_are_fixed(s: Snapshot) -> list[str]:
    """Inputs 20-26 report the controller's fixed outdoor points of the heat curve."""
    got = tuple(s.data.get(f"input_heat_curve_x_coordinate_{i}") for i in range(1, 8))
    return [] if got == CURVE_X else [f"curve x coordinates {got}, expected {CURVE_X}"]


def supply_limits_are_ordered(s: Snapshot) -> list[str]:
    """Each circuit's minimum supply limit lies below its maximum."""
    return [
        f"{label}: min {s.data[c['min']]} not below max {s.data[c['max']]}"
        for label, c in (("HC1", HC1), ("HC2", HC2))
        if not s.data[c["min"]] < s.data[c["max"]]
    ]


def curve_set_point(points: tuple[float, ...], outdoor: float, low: float, high: float) -> float:
    """Linear between the curve points, flat beyond the ends, clamped to the supply limits."""
    if outdoor >= CURVE_X[0]:
        value = points[0]
    elif outdoor <= CURVE_X[-1]:
        value = points[-1]
    else:
        i = next(i for i in range(len(CURVE_X) - 1) if CURVE_X[i + 1] <= outdoor <= CURVE_X[i])
        x0, x1 = CURVE_X[i], CURVE_X[i + 1]
        value = points[i] + (points[i + 1] - points[i]) * (x0 - outdoor) / (x0 - x1)
    return min(max(value, low), high)


def _set_point_violation(label: str, circuit: dict, s: Snapshot) -> list[str]:
    expected = curve_set_point(
        tuple(s.data[name] for name in circuit["curve"]), s.data[OUTDOOR], s.data[circuit["min"]], s.data[circuit["max"]]
    )
    got = s.data[circuit["set_point"]]
    if abs(got - expected) <= SET_POINT_TOLERANCE:
        return []
    return [f"{label} set point {got} °C, curve gives {expected:.2f} °C at outdoor {s.data[OUTDOOR]} °C"]


def hc1_set_point_follows_the_curve(s: Snapshot) -> list[str]:
    """HC1's supply set point (input 18) is its curve at the outdoor temperature, clamped (formal_review-1)."""
    return _set_point_violation("HC1", HC1, s)


def hc2_set_point_follows_the_curve(s: Snapshot) -> list[str]:
    """HC2's supply set point (input 147) follows its curve while it heats.

    Precondition: HC2 enabled and not in passive cooling (then input 147 is the cooling target).
    """
    if not s.data.get(HC2["enabled"]) or s.data.get(HC2["cooling"]):
        return []
    return _set_point_violation("HC2", HC2, s)


CHECKS: tuple[Callable[[Snapshot], list[str]], ...] = (
    firmware_is_a_captured_profile,
    every_register_reads,
    values_have_their_declared_type,
    temperatures_are_plausible,
    enum_values_are_documented,
    heat_curve_x_coordinates_are_fixed,
    supply_limits_are_ordered,
    hc1_set_point_follows_the_curve,
    hc2_set_point_follows_the_curve,
)
