"""Derived heating circuit 2 return temperature from a buffer energy balance (requirement-45).

Hermetic: the estimator takes update dicts and a condenser flow, and needs no device and no emulator.
"""

from __future__ import annotations

import math
import random

import pytest

from genesis_wp.derived import WATER, HeatingCircuit2ReturnEstimator, ReturnEstimate

pytestmark = pytest.mark.unit

COMPRESSOR = "dinput_compressor_control_signal"  # discrete input 199
PUMP = "dinput_mix_valve_1_circulation_pump_control_signal"  # discrete input 209, FR2
PASSIVE_ENABLED = "coil_enable_passive_cooling"  # coil 33
PASSIVE_ACTIVE = "dinput_indication_when_mixing_valve_1_is_producing_passive_cooling"  # discrete input 245
T8 = "input_condenser_in_temperature"
T9 = "input_condenser_out_temperature"
T31 = "input_buffer_tank_temperature"
T33 = "input_system_supply_line_temperature"
T32 = "input_mix_valve_1_supply_line_temperature"

VOLUME = 300.0  # L
CIRCUIT_FLOW = 1100 / 60  # L/min, 1.1 m³/h
CONDENSER_FLOW = 7.6  # L/min
VHC = WATER.volumetric_heat_capacity

BASE = {
    COMPRESSOR: True, PUMP: True, PASSIVE_ENABLED: False, PASSIVE_ACTIVE: False,
    T8: 28.25, T9: 31.53, T31: 27.0, T33: 30.0, T32: 28.1,
}
Q_CONDENSER = CONDENSER_FLOW / 60 * VHC * (31.53 - 28.25)  # kW


def estimator(**options) -> HeatingCircuit2ReturnEstimator:
    return HeatingCircuit2ReturnEstimator(VOLUME, CIRCUIT_FLOW, **options)


def warming(t: float, slope: float, data: dict = BASE) -> dict:
    """``data`` with T31 and T33 raised by ``slope`` [K/s] × ``t``, so their mean rises linearly."""
    return {**data, T31: data[T31] + slope * t, T33: data[T33] + slope * t}


def run(est, samples, start: float = 0.0, every: float = 60.0, flow=CONDENSER_FLOW) -> ReturnEstimate:
    """Add each sample ``every`` s from ``start``; returns the last estimate."""
    estimate = None
    for i, data in enumerate(samples):
        estimate = est.add(start + i * every, data, flow)
    return estimate


def expected(heat: float, supply: float = BASE[T32]) -> float:
    return supply - heat / (CIRCUIT_FLOW / 60 * VHC)


SLOPE = 0.5 / 1800  # K/s: the buffer mean rises 0.5 K per 30 min
WINDOW = [warming(60.0 * i, SLOPE) for i in range(31)]  # 1800 s at 60 s


# DHF-REQ: genesis-wp/requirement-45  DHF-TEST: genesis-wp/ac-175
def test_a_covered_window_yields_the_buffer_energy_balance_return_temperature():
    estimate = run(estimator(), WINDOW)
    heat = Q_CONDENSER - VOLUME * VHC * SLOPE
    assert estimate.heat == pytest.approx(heat, abs=1e-6)
    assert estimate.value == pytest.approx(expected(heat), abs=1e-6)
    assert BASE[T32] - estimate.value == pytest.approx(heat / (CIRCUIT_FLOW / 60 * VHC), abs=1e-6)


def test_the_window_slides_and_keeps_reporting_while_samples_stay_valid():
    est = estimator()
    estimates = [est.add(60.0 * i, warming(60.0 * i, SLOPE), CONDENSER_FLOW) for i in range(91)]
    assert all(e.value is None for e in estimates[:30])
    heat = Q_CONDENSER - VOLUME * VHC * SLOPE
    assert all(e.heat == pytest.approx(heat, abs=1e-6) for e in estimates[30:])


# DHF-REQ: genesis-wp/requirement-45  DHF-TEST: genesis-wp/ac-176
def test_samples_with_the_compressor_signal_off_add_no_condenser_heat():
    stopped = {COMPRESSOR: False, T8: None, T9: None}
    samples = [{**data, **stopped} if i % 2 else data for i, data in enumerate(WINDOW)]
    flows = [None if i % 2 else CONDENSER_FLOW for i in range(len(samples))]
    est = estimator()
    for i, (data, flow) in enumerate(zip(samples, flows)):
        estimate = est.add(60.0 * i, data, flow)
    # Each interval carries its closing sample's heat: 15 of the 30 intervals close on a running sample.
    heat = Q_CONDENSER / 2 - VOLUME * VHC * SLOPE
    assert estimate.heat == pytest.approx(heat, abs=1e-6)
    assert estimate.value == pytest.approx(expected(heat), abs=1e-6)


def test_a_cooling_buffer_adds_its_released_heat_to_the_circuit():
    cooling = [warming(60.0 * i, -SLOPE) for i in range(31)]
    estimate = run(estimator(), cooling)
    assert estimate.heat == pytest.approx(Q_CONDENSER + VOLUME * VHC * SLOPE, abs=1e-6)


DISTURBANCES = {
    "pump off": {PUMP: False},
    "pump unknown": {PUMP: None},
    "passive cooling enabled": {PASSIVE_ENABLED: True},
    "passive cooling enabled unknown": {PASSIVE_ENABLED: None},
    "passive cooling active": {PASSIVE_ACTIVE: True},
    "passive cooling active unknown": {PASSIVE_ACTIVE: None},
    "compressor unknown": {COMPRESSOR: None},
    "compressor not a bool": {COMPRESSOR: 1},
    "T31 missing": {T31: None},
    "T33 missing": {T33: math.nan},
    "T32 missing": {T32: None},
    "T8 missing while running": {T8: None},
    "T9 missing while running": {T9: math.inf},
}


def resume(est, start_index: int, count: int) -> list[ReturnEstimate]:
    return [est.add(60.0 * i, warming(60.0 * i, SLOPE), CONDENSER_FLOW) for i in range(start_index, start_index + count)]


# DHF-REQ: genesis-wp/requirement-45  DHF-TEST: genesis-wp/ac-177
@pytest.mark.parametrize("change", DISTURBANCES.values(), ids=DISTURBANCES.keys())
def test_one_invalid_sample_restarts_the_window(change):
    est = estimator()
    assert run(est, WINDOW).value is not None
    disturbed = est.add(60.0 * 31, {**warming(60.0 * 31, SLOPE), **change}, CONDENSER_FLOW)
    assert disturbed == ReturnEstimate(None, None)
    after = resume(est, 32, 31)  # 32 … 62: the window covers 1800 s again at sample 62
    assert all(e == ReturnEstimate(None, None) for e in after[:-1])
    assert after[-1].value is not None


def test_a_missing_condenser_flow_while_running_restarts_the_window():
    est = estimator()
    run(est, WINDOW)
    assert est.add(60.0 * 31, warming(60.0 * 31, SLOPE), None) == ReturnEstimate(None, None)


@pytest.mark.parametrize("data", [None, "not a dict", 42])
def test_an_update_that_is_not_a_mapping_restarts_the_window(data):
    est = estimator()
    run(est, WINDOW)
    assert est.add(60.0 * 31, data, CONDENSER_FLOW) == ReturnEstimate(None, None)


def test_a_gap_longer_than_max_gap_restarts_the_window():
    est = estimator()
    run(est, WINDOW)
    assert est.add(60.0 * 30 + 121.0, warming(60.0 * 32, SLOPE), CONDENSER_FLOW) == ReturnEstimate(None, None)


def test_a_clock_step_back_restarts_the_window_and_a_repeated_timestamp_is_dropped():
    est = estimator()
    covered = run(est, WINDOW)
    assert est.add(60.0 * 30, warming(60.0 * 30, SLOPE), CONDENSER_FLOW) == covered  # repeated: dropped
    assert est.add(0.0, BASE, CONDENSER_FLOW) == ReturnEstimate(None, None)


def test_a_buffer_taking_more_heat_than_the_condenser_supplies_reports_none():
    steep = [warming(60.0 * i, 10 * SLOPE) for i in range(31)]  # Q_store ≈ 7 kW > Q_cond ≈ 1.7 kW
    assert run(estimator(), steep) == ReturnEstimate(None, None)


# Hostile data (ac-178).
USED = (COMPRESSOR, PUMP, PASSIVE_ENABLED, PASSIVE_ACTIVE, T8, T9, T31, T33, T32)
FLOWS = (CONDENSER_FLOW, None, math.nan, math.inf, -1.0, 0.0, True, "7.6", 10**400, 1e308)


def hostile_samples() -> list[dict]:
    samples = [
        {**BASE, T31: 1e308, T33: 1e308}, {**BASE, T32: -1e308}, {**BASE, T9: 1e308, T8: -1e308},
        {**BASE, T31: 10**400}, {**BASE, T32: True}, {**BASE, T31: "27.0"}, {**BASE, COMPRESSOR: False},
        {**BASE, COMPRESSOR: "on"}, {**BASE, T9: 20.0}, BASE,
    ]
    samples += [{**BASE, key: None} for key in USED]
    samples += [{k: v for k, v in BASE.items() if k != key} for key in USED]
    return samples


def random_timeline(seed: int, length: int = 3000):
    rng = random.Random(seed)
    pool = hostile_samples()
    t, timeline = 0.0, []
    while len(timeline) < length:
        if rng.random() < 0.05:  # a full valid window, so hostile samples land inside one
            slope = rng.choice((SLOPE, -SLOPE, 10 * SLOPE, 0.0, 1e300))
            timeline += [(t + 60.0 * (i + 1), warming(60.0 * (i + 1), slope), CONDENSER_FLOW) for i in range(31)]
            t = timeline[-1][0]
            continue
        step = rng.choice((60.0, 60.0, 30.0, 0.0, -60.0, 1e-9, 600.0, math.nan))
        t = t if math.isnan(step) else t + step  # a NaN timestamp is emitted, the clock itself moves on
        data = rng.choice(pool + [None, "x", {}])
        timeline.append((t + step if math.isnan(step) else t, data, rng.choice(FLOWS)))
    return timeline


def assert_bounded(est, timeline) -> int:
    reported = 0
    for timestamp, data, flow in timeline:
        estimate = est.add(timestamp, data, flow)
        assert (estimate.value is None) == (estimate.heat is None)
        if estimate.value is not None:
            assert type(estimate.value) is float and math.isfinite(estimate.value)
            assert type(estimate.heat) is float and math.isfinite(estimate.heat) and estimate.heat >= 0
            reported += 1
    return reported


# DHF-REQ: genesis-wp/requirement-45  DHF-TEST: genesis-wp/ac-178
@pytest.mark.parametrize("seed", range(5))
def test_no_input_sequence_yields_a_non_finite_value_a_negative_heat_or_an_exception(seed):
    assert assert_bounded(estimator(), random_timeline(seed)) > 0


def test_hostile_samples_inside_valid_windows_stay_bounded():
    timeline = []
    for data in hostile_samples():
        for flow in FLOWS:
            start = len(timeline) * 60.0
            timeline += [(start + 60.0 * i, warming(60.0 * i, SLOPE), CONDENSER_FLOW) for i in range(31)]
            timeline.append((len(timeline) * 60.0, data, flow))
    timestamps = [(math.nan, BASE, CONDENSER_FLOW), (None, BASE, CONDENSER_FLOW), ("later", BASE, CONDENSER_FLOW),
                  (math.inf, BASE, CONDENSER_FLOW), (True, BASE, CONDENSER_FLOW)]
    assert assert_bounded(estimator(), timeline + timestamps) > 0


INVALID_CONSTANTS = (math.nan, math.inf, -math.inf, 0, 0.0, -1.0, True, "300", None, 10**400)


# DHF-REQ: genesis-wp/requirement-45  DHF-TEST: genesis-wp/ac-179
def test_the_plant_constants_are_required():
    with pytest.raises(TypeError):
        HeatingCircuit2ReturnEstimator()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        HeatingCircuit2ReturnEstimator(VOLUME)  # type: ignore[call-arg]


@pytest.mark.parametrize("value", INVALID_CONSTANTS)
@pytest.mark.parametrize("position", [0, 1])
def test_the_constructor_rejects_an_implausible_plant_constant(value, position):
    arguments = [VOLUME, CIRCUIT_FLOW]
    arguments[position] = value
    with pytest.raises(ValueError):
        HeatingCircuit2ReturnEstimator(*arguments)


@pytest.mark.parametrize("options", [
    {"window_s": 0}, {"window_s": -1.0}, {"window_s": math.nan}, {"window_s": True},
    {"edge_s": 0}, {"edge_s": math.inf}, {"edge_s": 900}, {"edge_s": 1000},
    {"max_gap_s": 0}, {"max_gap_s": 1800}, {"max_gap_s": "120"},
    {"fluid": "water"},
])
def test_the_constructor_rejects_invalid_window_settings(options):
    with pytest.raises(ValueError):
        estimator(**options)


def test_the_constructor_keeps_valid_constants_and_defaults():
    est = estimator()
    assert (est.buffer_volume, est.circuit_flow) == (VOLUME, CIRCUIT_FLOW)
    assert (est.window_s, est.edge_s, est.max_gap_s, est.fluid) == (1800.0, 180.0, 120.0, WATER)
    assert estimator(window_s=600, edge_s=60, max_gap_s=30).window_s == 600.0
