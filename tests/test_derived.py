"""Derived flow estimators for the condenser and brine loops (requirement-31).

Hermetic: the estimators take update dicts and need no device and no emulator.
"""

from __future__ import annotations

import math
import random

import pytest

from genesis_wp.derived import BRINE, BRINE_MEG25, CONDENSER, WATER, FlowEstimator, Fluid

pytestmark = pytest.mark.unit

SPEED = "input_compressor_speed_rpm"
HEAT = "input_current_power_output"
POWER = "input_current_consumed_power"
CONDENSER_IN = "input_condenser_in_temperature"
CONDENSER_OUT = "input_condenser_out_temperature"
BRINE_IN = "input_brine_in_temperature"
BRINE_OUT = "input_brine_out_temperature"
CONDENSER_PUMP = "input_condenser_circulation_pump_speed"
BRINE_PUMP = "input_brine_circulation_pump_speed"

# The read-only read 2026-10-29 20:27 UTC (requirement-31 rationale).
STEADY = {
    SPEED: 1798, HEAT: 1.73, POWER: 0.28,
    CONDENSER_IN: 28.25, CONDENSER_OUT: 31.53, BRINE_IN: 7.86, BRINE_OUT: 5.60,
    CONDENSER_PUMP: 35.0, BRINE_PUMP: 47.62,
}
CONDENSER_STEADY = 60 * 1.73 / (1.00 * 4.18 * 3.28)
BRINE_STEADY = 60 * 1.45 / (1.04 * 3.80 * 2.26)
STOPPED = {**STEADY, SPEED: 0, HEAT: 0.0, POWER: 0.0, CONDENSER_PUMP: 30.0}


def feed(estimator: FlowEstimator, data: dict, start: float = 0.0, seconds: float = 360, every: float = 30):
    """Add ``data`` every ``every`` s from ``start`` through ``start + seconds``; returns the last estimate."""
    estimate = None
    for i in range(int(seconds // every) + 1):
        estimate = estimator.add(start + i * every, data)
    return estimate


# DHF-REQ: genesis-wp/requirement-31  DHF-TEST: genesis-wp/ac-117
def test_steady_running_window_yields_the_energy_balance_flow_for_both_loops():
    condenser = feed(FlowEstimator(CONDENSER), STEADY)
    brine = feed(FlowEstimator(BRINE), STEADY)
    assert condenser.source == "calculated" and condenser.value == pytest.approx(CONDENSER_STEADY, rel=0.01)
    assert brine.source == "calculated" and brine.value == pytest.approx(BRINE_STEADY, rel=0.01)
    assert CONDENSER_STEADY == pytest.approx(7.6, abs=0.05) and BRINE_STEADY == pytest.approx(9.7, abs=0.05)


def condenser_delta(delta_t: float) -> dict:
    return {**STEADY, CONDENSER_IN: STEADY[CONDENSER_OUT] - delta_t}


# DHF-REQ: genesis-wp/requirement-31  DHF-TEST: genesis-wp/ac-119
def test_window_below_the_temperature_difference_threshold_yields_no_calculated_value():
    below = feed(FlowEstimator(CONDENSER), condenser_delta(1.9), seconds=300)
    above = feed(FlowEstimator(CONDENSER), condenser_delta(2.1), seconds=300)
    assert below.value is None and below.source == "unknown"
    assert above.source == "calculated" and math.isfinite(above.value)
    assert above.value == pytest.approx(60 * 1.73 / (4.18 * 2.1), rel=0.01)


# DHF-REQ: genesis-wp/requirement-31  DHF-TEST: genesis-wp/ac-120
def test_resting_compressor_reports_flow_from_pump_speed_once_calibrated():
    estimator = FlowEstimator(CONDENSER)
    calculated = feed(estimator, STEADY)
    assert calculated.source == "calculated"
    flow = calculated.value
    assert calculated.calibration == pytest.approx(flow / 35, rel=1e-9)

    resting = estimator.add(400, STOPPED)
    assert resting.source == "pump_speed" and resting.value == pytest.approx(flow / 35 * 30, rel=0.01)
    idle = estimator.add(430, {**STOPPED, CONDENSER_PUMP: 0.0})
    assert idle.source == "pump_speed" and idle.value == 0.0

    uncalibrated = FlowEstimator(CONDENSER)
    for t, data in ((400, STOPPED), (430, {**STOPPED, CONDENSER_PUMP: 0.0})):
        estimate = uncalibrated.add(t, data)
        assert estimate.value is None and estimate.source == "unknown" and estimate.calibration is None


# DHF-REQ: genesis-wp/requirement-31  DHF-TEST: genesis-wp/ac-122
def test_fluid_defaults_cite_their_source_and_can_be_overridden():
    assert (WATER.cp, WATER.density) == (4.18, 1.00)
    assert (BRINE_MEG25.cp, BRINE_MEG25.density) == (3.80, 1.04)
    assert "25 % ethylene glycol" in BRINE_MEG25.source
    assert "WPE-I installation manual" in BRINE_MEG25.source and "7.7.3" in BRINE_MEG25.source
    assert CONDENSER.fluid is WATER and BRINE.fluid is BRINE_MEG25

    half = Fluid(cp=BRINE_MEG25.cp / 2, density=BRINE_MEG25.density, source="half the volumetric heat capacity")
    default = feed(FlowEstimator(BRINE), STEADY)
    supplied = feed(FlowEstimator(BRINE, fluid=half), STEADY)
    assert supplied.source == default.source == "calculated"
    assert supplied.value == pytest.approx(2 * default.value, rel=0.01)


# ---- ac-118: no input makes the estimate non-finite, negative or above the maximum ----

MAXIMUM = 25.0
USED = (SPEED, HEAT, POWER, CONDENSER_IN, CONDENSER_OUT, BRINE_IN, BRINE_OUT, CONDENSER_PUMP, BRINE_PUMP)


def hostile_samples() -> list[dict]:
    """Samples the energy balance must survive, one disturbance each."""
    samples = [
        {**STEADY, CONDENSER_IN: STEADY[CONDENSER_OUT], BRINE_IN: STEADY[BRINE_OUT]},  # ΔT = 0
        {**STEADY, CONDENSER_IN: 40.0, BRINE_IN: 2.0},  # negative ΔT
        {**STEADY, CONDENSER_IN: STEADY[CONDENSER_OUT] - 1e-6, BRINE_IN: STEADY[BRINE_OUT] + 1e-6},  # tiny ΔT
        {**STEADY, HEAT: 0.0},
        {**STEADY, HEAT: 0.10, POWER: 0.28},  # heat output below electric power
        {**STEADY, SPEED: 0},  # stopped compressor
        {**STEADY, CONDENSER_PUMP: 250.0, BRINE_PUMP: -5.0},  # pump out of range
        {**STEADY, HEAT: 500.0},  # implausible heat: flow above the maximum
        {**STEADY, HEAT: math.inf}, {**STEADY, CONDENSER_IN: math.nan}, {**STEADY, SPEED: True},
        {**STEADY, HEAT: "1.73"},
        STEADY,
    ]
    samples += [{**STEADY, key: None} for key in USED]
    samples += [{k: v for k, v in STEADY.items() if k != key} for key in USED]  # missing key
    return samples


def assert_bounded(estimator: FlowEstimator, timeline) -> int:
    count = 0
    for timestamp, data in timeline:
        estimate = estimator.add(timestamp, data)
        count += 1
        assert estimate.source in ("calculated", "pump_speed", "unknown")
        if estimate.value is None:
            assert estimate.source == "unknown"
        else:
            assert type(estimate.value) is float and math.isfinite(estimate.value)
            assert 0.0 <= estimate.value <= MAXIMUM
    return count


def fixed_timeline() -> list[tuple[float, dict]]:
    timeline = [(30.0 * i, STEADY) for i in range(12)]  # calibrate first, so the fallback is exercised
    t = timeline[-1][0]
    for data in hostile_samples():
        t += 30.0
        timeline += [(t, data), (t, STEADY), (t - 60.0, STEADY)]  # repeated and earlier timestamp
    timeline += [(math.nan, STEADY), (math.inf, STEADY), (None, STEADY), ("later", STEADY)]
    timeline += [(t + 30.0, None), (t + 60.0, "not a dict"), (t + 90.0, {})]
    return timeline


def random_timeline(seed: int, length: int = 1500) -> list[tuple[float, dict]]:
    rng = random.Random(seed)
    pool = hostile_samples() + [STEADY] * 10 + [condenser_delta(2.1)] * 5
    t, timeline = 0.0, []
    for _ in range(length):
        t += rng.choice((30.0, 30.0, 30.0, 0.0, -30.0, 1e-9, 600.0))
        timeline.append((t, rng.choice(pool)))
    return timeline


# DHF-REQ: genesis-wp/requirement-31  DHF-TEST: genesis-wp/ac-118
@pytest.mark.parametrize("loop", [CONDENSER, BRINE], ids=lambda loop: loop.name)
def test_no_input_sequence_makes_the_estimate_non_finite_negative_or_above_the_maximum(loop):
    assert assert_bounded(FlowEstimator(loop, max_flow=MAXIMUM), fixed_timeline()) > 60
    for seed in (1, 2, 3):
        assert assert_bounded(FlowEstimator(loop, max_flow=MAXIMUM), random_timeline(seed)) >= 1000


def test_flow_above_the_maximum_is_rejected_not_clamped():
    implausible = feed(FlowEstimator(CONDENSER, max_flow=5.0), STEADY)  # 7.6 L/min > 5
    assert implausible.value is None and implausible.source == "unknown"


def test_window_must_cover_its_length_before_a_value_is_calculated():
    short = feed(FlowEstimator(CONDENSER), STEADY, seconds=270)
    assert short.value is None and short.source == "unknown"


def test_a_stopped_compressor_restarts_the_window():
    estimator = FlowEstimator(CONDENSER)
    feed(estimator, STEADY)
    estimator.add(400, STOPPED)
    restarted = feed(estimator, STEADY, start=430, seconds=120)
    assert restarted.source == "pump_speed"  # calibrated, but the new run is shorter than the window


@pytest.mark.parametrize("options", [
    {"window_s": 0}, {"window_s": math.inf}, {"min_delta_t": 0}, {"min_delta_t": -1}, {"max_flow": math.nan},
    {"max_flow": 0}, {"fluid": "water"},
])
def test_constructor_rejects_invalid_arguments(options):
    with pytest.raises(ValueError):
        FlowEstimator(CONDENSER, **options)


@pytest.mark.parametrize("cp, density", [(0.0, 1.0), (4.18, -1.0), (math.nan, 1.0), (4.18, math.inf)])
def test_fluid_rejects_non_positive_or_non_finite_properties(cp, density):
    with pytest.raises(ValueError):
        Fluid(cp=cp, density=density, source="x")


def test_constructor_rejects_an_unknown_loop():
    with pytest.raises(ValueError):
        FlowEstimator("condenser")
