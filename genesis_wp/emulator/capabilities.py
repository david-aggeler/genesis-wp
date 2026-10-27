"""Capability selection: present a plant with or without each module (requirement-14).

A selection ``{capability: True|False}`` becomes raw value overrides on top of a profile,
derived from the library's own detection rules (``genesis_wp.capabilities``), so the
library detects exactly the selection. Values a switched-on module needs that the capture
does not contain are synthetic; they are listed in the served profile's source notes and
never written back to a profile file.
"""

from __future__ import annotations

from collections.abc import Mapping

from .. import capabilities as lib
from ..const import (
    KEY_ADDRESS,
    KEY_NOT_FITTED,
    KEY_REG_TYPE,
    KEY_SCALE,
    REG_COIL,
    REG_DISCRETE_INPUT,
    REG_HOLDING,
    REG_INPUT,
    REGISTERS,
)
from .profile import COILS, DISCRETE_INPUTS, HOLDING_REGISTERS, INPUT_REGISTERS, Profile, ProfileError

BLOCK_OF = {REG_COIL: COILS, REG_DISCRETE_INPUT: DISCRETE_INPUTS, REG_HOLDING: HOLDING_REGISTERS, REG_INPUT: INPUT_REGISTERS}

# Plausible readings [°C] for a sensor the capture has as "not fitted".
SYNTHETIC_TEMPERATURES = {
    "input_tap_water_top_temperature": 50.0,
    "input_tap_water_lower_temperature": 45.0,
    "input_tap_water_weighted_temperature": 48.0,
    "input_mix_valve_1_supply_line_temperature": 30.0,
    "input_mix_valve_2_supply_line_temperature": 30.0,
    "input_mix_valve_3_supply_line_temperature": 30.0,
    "input_buffer_tank_temperature": 30.0,
    "input_pool_supply_line_temperature": 26.0,
    "input_pool_return_line_temperature": 25.0,
    "input_cooling_circuit_supply_line_temperature": 18.0,
    "input_room_temperature_sensor": 21.0,
}
METER_PHASE_VOLTAGE = 230.0  # [V], line to neutral; line-to-line 400 V does not fit the int16 register at scale 100

# Signals a switched-on special-rule capability shows: register -> decoded value.
SPECIAL_ON = {
    # SG Ready load-up active: DI22 closed, mode load-up, smart-grid state comfort (design_decision-3).
    "smart_grid": {"dinput_smart_grid_2": 1, "holding_power_consumption_control": 1, "input_comfort_mode": 5},
    "additional_heater": {"coil_enable_external_additional_heater": 1},
}


class CapabilityError(ProfileError):
    """A capability selection the model or the detection rules cannot present."""


def _key(name: str) -> tuple[str, int]:
    meta = REGISTERS[name]
    return BLOCK_OF[meta[KEY_REG_TYPE]], meta[KEY_ADDRESS]


def _raw(name: str, value: float) -> int:
    raw = round(value * REGISTERS[name][KEY_SCALE])
    return raw & 0xFFFF


def _enabled(name: str | None, kind: str) -> bool:
    return name is not None and bool(REGISTERS[name][kind])


def selection_overrides(profile: Profile, selection: Mapping[str, bool]) -> tuple[dict[tuple[str, int], int], list[str]]:
    """Raw overrides presenting ``selection`` on ``profile``, and notes naming the synthetic values."""
    kind = profile.model
    overrides: dict[tuple[str, int], int] = {}
    notes: list[str] = []
    for name, on in selection.items():
        rule = next((r for r in lib.CAPABILITY_RULES if r.name == name), None)
        if rule is None:
            raise CapabilityError(f"unknown capability {name!r}; known: {', '.join(lib.CAPABILITY_NAMES)}")
        if not lib.gated_registers(rule, kind):
            raise CapabilityError(f"capability {name!r}: model {kind} has no registers for it")
        if rule.rule == lib.RULE_ANY_OF:
            if on:
                raise CapabilityError(
                    f"capability {name!r} combines {' or '.join(rule.members)}; switch on one of those instead"
                )
            # Off: every member off (members the model lacks are absent already).
            members = {m: False for m in rule.members if lib.gated_registers(_rule(m), kind)}
            sub, sub_notes = selection_overrides(profile, members)
            overrides.update(sub)
            notes.extend(sub_notes)
            continue
        changes = _module(rule, kind, on, profile) if rule.rule == lib.RULE_MODULE else _special(rule, kind, on)
        synthetic = []
        for register, raw in changes.items():
            key = _key(register)
            overrides[key] = raw
            if raw != profile.blocks[key[0]].value(key[1]):
                synthetic.append(f"{key[0]} {key[1]} = {raw}")
        state = "on" if on else "off"
        notes.append(
            f"capability selection {name}={state} (synthetic, not captured): {', '.join(synthetic) or 'no value changed'}"
        )
    return overrides, notes


def _rule(name: str) -> lib.CapabilityRule:
    return next(r for r in lib.CAPABILITY_RULES if r.name == name)


def _module(rule: lib.CapabilityRule, kind: str, on: bool, profile: Profile) -> dict[str, int]:
    changes = {}
    if _enabled(rule.enable, kind):
        changes[rule.enable] = int(on)
    sensors = [s for s in rule.sensors if _enabled(s, kind) and REGISTERS[s].get(KEY_NOT_FITTED, {}).get(kind) is not None]
    if not on and not sensors:
        # An enable coil that is off means switched off, not missing (design_decision-2).
        raise CapabilityError(
            f"capability {rule.name!r} cannot be switched off: no sensor proves its absence, so detection reports unknown"
        )
    if not changes and not sensors:
        raise CapabilityError(f"capability {rule.name!r}: model {kind} has no enable coil or declared sensor to present it")
    for sensor in sensors:
        sentinel = REGISTERS[sensor][KEY_NOT_FITTED][kind]
        if not on:
            changes[sensor] = sentinel
        elif profile.blocks[_key(sensor)[0]].value(_key(sensor)[1]) == sentinel:
            changes[sensor] = _raw(sensor, SYNTHETIC_TEMPERATURES[sensor])
    return changes


def _special(rule: lib.CapabilityRule, kind: str, on: bool) -> dict[str, int]:
    if rule.rule == lib.RULE_BUILT_IN:
        if not on:
            raise CapabilityError(f"capability {rule.name!r} is built into every {kind} unit and cannot be switched off")
        return {}
    if rule.rule == lib.RULE_ANY_POSITIVE:
        signals = [s for s in rule.signals if _enabled(s, kind)]
        on_value = {s: METER_PHASE_VOLTAGE if "_0_voltage" in s else 0.0 for s in signals}
        return {s: _raw(s, on_value[s] if on else 0.0) for s in signals}
    if not on:
        # RULE_ANY_SET / RULE_SEEN_NONZERO: all-zero signals read as unknown, never absent.
        raise CapabilityError(
            f"capability {rule.name!r} cannot be switched off: with its signals at 0 detection reports unknown, not absent"
        )
    return {s: _raw(s, v) for s, v in SPECIAL_ON[rule.name].items() if _enabled(s, kind)}


def parse_selection(specs: list[str]) -> dict[str, bool]:
    """Parse ``NAME=on|off`` items."""
    selection = {}
    for spec in specs:
        name, sep, state = spec.partition("=")
        if not sep or state not in ("on", "off") or not name:
            raise CapabilityError(f"bad capability {spec!r}, expected NAME=on or NAME=off")
        selection[name] = state == "on"
    return selection
