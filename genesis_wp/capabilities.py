"""Plant capabilities: which modules a plant has, from values the library already reads.

requirement-13, design_decision-2. The controller has no "installed modules" register, so
each capability is decided by an evidence rule over read-only values (function codes 1-4):

1. Model support: a model without registers for the module does not report it
   (:func:`capability_state` answers absent).
2. Its sensors, where the model declares a "not fitted" sentinel: any fitted means present,
   all not fitted means absent. A sensor without a declared sentinel is no evidence.
3. Otherwise its enable coil: on means present (a commissioner enables only fitted modules).
   Off means switched off, not missing (design_decision-2, revised 2026-10-27).
4. Otherwise a running signal seen during the client's lifetime means present; else unknown.

``Capability.enabled`` reports the enable coil separately: installed and switched on are
different questions.

Some modules have their own rule (see :data:`CAPABILITY_RULES`). A consumer may override
any capability; the library never writes to the device to find out.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .const import KEY_ADDRESS, KEY_NOT_FITTED, KEY_REG_TYPE, REGISTERS
from .groups import REGISTER_GROUPS, register_category, register_concerns

PRESENT = "present"
ABSENT = "absent"
UNKNOWN = "unknown"
STATES = (PRESENT, ABSENT, UNKNOWN)

# Evidence rules
RULE_MODULE = "module"  # enable coil, then sensors with a declared not-fitted sentinel
RULE_BUILT_IN = "built_in"  # part of every unit of a model that has its registers
RULE_ANY_POSITIVE = "any_positive"  # present if any signal is above 0, absent if all read 0
RULE_ANY_SET = "any_set"  # present if any signal is non-zero, else unknown
RULE_SEEN_NONZERO = "seen_nonzero"  # unknown until a signal is seen non-zero, then present for the client's lifetime
RULE_ANY_OF = "any_of"  # combined: present if any member is present, absent if all are absent, else unknown


@dataclass(frozen=True)
class CapabilityRule:
    """How one capability is detected, and which registers it gates."""

    name: str
    #: Groups whose registers (and the alarms concerning them) the capability gates.
    groups: tuple[str, ...] = ()
    #: Categories whose registers the capability gates (design_decision-4).
    categories: tuple[str, ...] = ()
    #: Member capabilities (RULE_ANY_OF).
    members: tuple[str, ...] = ()
    #: Signals that prove the module operates (RULE_MODULE); once seen, present for the client's lifetime.
    running: tuple[str, ...] = ()
    rule: str = RULE_MODULE
    #: Enable coil (RULE_MODULE).
    enable: str | None = None
    #: Sensors whose not-fitted sentinel is evidence (RULE_MODULE).
    sensors: tuple[str, ...] = ()
    #: Registers the special rules read.
    signals: tuple[str, ...] = ()
    #: Name fragments gated regardless of group, and fragments taken out of the gated groups.
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class Capability:
    """The detected state of one capability after an update."""

    name: str
    state: str
    #: The registers and values that decided the state, e.g. ``coil 8 coil_enable_tap_water = 0``.
    evidence: tuple[str, ...]
    #: Registers enabled for the model that belong to this capability.
    gates: tuple[str, ...]
    overridden: bool = False
    #: Switched on: the module's enable coil (None when it has none or it was not read).
    enabled: bool | None = None


CAPABILITY_RULES = (
    CapabilityRule(
        "heating_circuit_2", groups=("heating_circuit_2",), enable="coil_enable_mix_valve_1",
        sensors=("input_mix_valve_1_supply_line_temperature",),
    ),
    CapabilityRule(
        "heating_circuit_3", groups=("heating_circuit_3",), enable="coil_enable_mix_valve_2",
        sensors=("input_mix_valve_2_supply_line_temperature",),
    ),
    CapabilityRule(
        "heating_circuit_4", groups=("heating_circuit_4",), enable="coil_enable_mix_valve_3",
        sensors=("input_mix_valve_3_supply_line_temperature",),
    ),
    CapabilityRule("buffer_tank", groups=("buffer_tank",), sensors=("input_buffer_tank_temperature",)),
    CapabilityRule(
        "hot_water", groups=("hot_water",), enable="coil_enable_tap_water",
        sensors=("input_tap_water_top_temperature", "input_tap_water_lower_temperature", "input_tap_water_weighted_temperature"),
    ),
    CapabilityRule(
        "hot_gas_recovery", groups=("hot_gas_recovery",), enable="coil_enable_hgw",
        sensors=("input_hgw_supply_line_temperature",),
    ),
    CapabilityRule(
        "pool", groups=("pool",), enable="coil_enable_pool",
        sensors=("input_pool_supply_line_temperature", "input_pool_return_line_temperature"),
    ),
    CapabilityRule(
        "active_cooling", groups=("cooling",), enable="coil_enable_active_cooling",
        sensors=("input_cooling_circuit_supply_line_temperature",), exclude=("passive",),
    ),
    CapabilityRule(
        "passive_cooling", enable="coil_enable_passive_cooling", include=("passive",),
        running=("dinput_indication_when_mixing_valve_1_is_producing_passive_cooling",),
    ),
    CapabilityRule("cooling", categories=("cooling",), rule=RULE_ANY_OF, members=("active_cooling", "passive_cooling")),
    CapabilityRule(
        "room_sensor", sensors=("input_room_temperature_sensor",),
        include=("room_temperature_sensor", "room_sensor"),
    ),
    CapabilityRule(
        "additional_heater", groups=("additional_heater",), rule=RULE_ANY_SET,
        signals=("coil_enable_external_additional_heater", "coil_enable_internal_additional_heater", "holding_enabled_immersion_heater"),
    ),
    CapabilityRule(
        "electric_meter", groups=("electric_meter",), rule=RULE_ANY_POSITIVE,
        signals=(
            "input_electric_meter_l1_0_voltage", "input_electric_meter_l2_0_voltage", "input_electric_meter_l3_0_voltage",
            "input_electric_meter_l1_l2_voltage", "input_electric_meter_l2_l3_voltage", "input_electric_meter_l3_l1_voltage",
        ),
    ),
    CapabilityRule(
        "smart_grid", groups=("power_consumption_control",), rule=RULE_SEEN_NONZERO,
        signals=("dinput_smart_grid_2", "holding_power_consumption_control"),
    ),
    CapabilityRule("ksm", groups=("ksm",), rule=RULE_BUILT_IN),
)
CAPABILITY_NAMES = tuple(rule.name for rule in CAPABILITY_RULES)
_RULES = {rule.name: rule for rule in CAPABILITY_RULES}


def gated_registers(rule: CapabilityRule, kind: str) -> tuple[str, ...]:
    """Registers enabled for ``kind`` that belong to the capability."""
    names = []
    for name, meta in REGISTERS.items():
        if not meta[kind] or any(fragment in name for fragment in rule.exclude):
            continue
        group = REGISTER_GROUPS[name]
        if (
            group in rule.groups
            or register_concerns(name) in rule.groups
            or register_category(name) in rule.categories
            or any(fragment in name for fragment in rule.include)
            or name == rule.enable
        ):
            names.append(name)
    return tuple(names)


def _enabled(name: str | None, kind: str) -> bool:
    return name is not None and bool(REGISTERS[name][kind])


def _ref(name: str) -> str:
    meta = REGISTERS[name]
    return f"{meta[KEY_REG_TYPE]} {meta[KEY_ADDRESS]} {name}"


def _evidence(name: str, data: Mapping) -> str:
    if name not in data:
        return f"{_ref(name)} not read"
    value = data[name]
    if value is None:
        return f"{_ref(name)} = not fitted"
    if isinstance(value, bool):
        value = int(value)
    return f"{_ref(name)} = {value}"


def _module(rule: CapabilityRule, kind: str, data: Mapping, seen: set[str]) -> tuple[str, list[str], bool | None]:
    evidence, coil = _coil(rule, kind, data)
    fitted = _fitted(rule, kind, data, evidence)
    running = [name for name in rule.running if _enabled(name, kind)]
    running_now = any(data.get(name) for name in running)
    if running_now:
        seen.add(rule.name)  # recorded whatever decides the state now, so it holds after switch-off
    if fitted:
        return (PRESENT if any(fitted) else ABSENT), evidence, coil
    if coil:
        return PRESENT, evidence, coil
    evidence.extend(_evidence(name, data) for name in running)
    if rule.name in seen and not running_now:
        evidence.append("seen running in an earlier update")
    return (PRESENT if rule.name in seen else UNKNOWN), evidence, coil


def _coil(rule: CapabilityRule, kind: str, data: Mapping) -> tuple[list[str], bool | None]:
    """Evidence and state of the module's enable coil (None when it has none or it was not read)."""
    if not _enabled(rule.enable, kind):
        return [], None
    return [_evidence(rule.enable, data)], bool(data[rule.enable]) if rule.enable in data else None


def _fitted(rule: CapabilityRule, kind: str, data: Mapping, evidence: list[str]) -> list[bool]:
    """Fitted flags of the read sensors with a declared not-fitted sentinel; appends their evidence."""
    fitted = []
    for sensor in rule.sensors:
        if not _enabled(sensor, kind) or REGISTERS[sensor].get(KEY_NOT_FITTED, {}).get(kind) is None:
            continue  # no declared sentinel: the value says nothing about the sensor being fitted
        evidence.append(_evidence(sensor, data))
        if sensor in data:
            fitted.append(data[sensor] is not None)
    return fitted


def _signals(rule: CapabilityRule, kind: str, data: Mapping) -> tuple[list, list[str]]:
    names = [name for name in rule.signals if _enabled(name, kind)]
    return [data[name] for name in names if name in data and data[name] is not None], [_evidence(n, data) for n in names]


def _built_in(rule, kind, data, seen, gates, result):
    return PRESENT, [f"built into every {kind} unit ({len(gates)} registers)"], None


def _any_positive(rule, kind, data, seen, gates, result):
    values, evidence = _signals(rule, kind, data)
    return (PRESENT if any(v > 0 for v in values) else ABSENT if values else UNKNOWN), evidence, None


def _any_set(rule, kind, data, seen, gates, result):
    values, evidence = _signals(rule, kind, data)
    return (PRESENT if any(values) else UNKNOWN), evidence, None


def _any_of(rule, kind, data, seen, gates, result):
    members = {m: result[m].state if m in result else ABSENT for m in rule.members}  # members precede it
    states = set(members.values())
    state = PRESENT if PRESENT in states else ABSENT if states == {ABSENT} else UNKNOWN
    return state, [f"{m} = {s}" for m, s in members.items()], None


def _seen_nonzero(rule, kind, data, seen, gates, result):
    values, evidence = _signals(rule, kind, data)
    if any(values):
        seen.add(rule.name)
    elif rule.name in seen:
        evidence.append("seen non-zero in an earlier update")
    return (PRESENT if rule.name in seen else UNKNOWN), evidence, None


def _module_rule(rule, kind, data, seen, gates, result):
    return _module(rule, kind, data, seen)


# Evidence rule -> evaluator(rule, kind, data, seen, gates, result) -> (state, evidence, enabled).
_EVALUATORS = {
    RULE_MODULE: _module_rule,
    RULE_BUILT_IN: _built_in,
    RULE_ANY_POSITIVE: _any_positive,
    RULE_ANY_SET: _any_set,
    RULE_ANY_OF: _any_of,
    RULE_SEEN_NONZERO: _seen_nonzero,
}


def evaluate(
    kind: str,
    data: Mapping,
    *,
    overrides: Mapping[str, str] | None = None,
    seen: set[str] | None = None,
) -> dict[str, Capability]:
    """Capabilities of a ``kind`` plant from one update's decoded ``data``.

    Only capabilities the model has registers for are returned. ``seen`` carries the
    capabilities a RULE_SEEN_NONZERO signal has shown before; it is updated in place.
    """
    overrides = validate_overrides(overrides)
    seen = set() if seen is None else seen
    result = {}
    for rule in CAPABILITY_RULES:
        gates = gated_registers(rule, kind)
        if not gates:
            continue
        state, evidence, enabled = _EVALUATORS[rule.rule](rule, kind, data, seen, gates, result)
        if not evidence:
            evidence = [f"no evidence register enabled for {kind}"]
        overridden = rule.name in overrides
        if overridden:
            evidence = [f"overridden by the consumer to {overrides[rule.name]} (detected {state})", *evidence]
            state = overrides[rule.name]
        result[rule.name] = Capability(rule.name, state, tuple(evidence), gates, overridden, enabled)
    return result


def validate_overrides(overrides: Mapping[str, str] | None) -> dict[str, str]:
    """Check consumer overrides: known capability names, states from :data:`STATES`."""
    overrides = dict(overrides or {})
    for name, state in overrides.items():
        if name not in _RULES:
            raise ValueError(f"unknown capability {name!r}; known: {', '.join(CAPABILITY_NAMES)}")
        if state not in STATES:
            raise ValueError(f"capability {name}: state must be one of {', '.join(STATES)}, not {state!r}")
    return overrides
