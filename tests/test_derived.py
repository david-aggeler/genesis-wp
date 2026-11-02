"""Derived flow estimators for the condenser and brine loops (requirement-31).

Hermetic: the estimators take update dicts and need no device and no emulator.
"""

from __future__ import annotations

import logging
import math
import random
import re

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


# DHF-REQ: genesis-wp/requirement-31  DHF-TEST: genesis-wp/ac-135
def test_varying_inputs_yield_the_ratio_of_window_sums_not_a_mean_of_per_sample_ratios():
    high, low = {**condenser_delta(2.0), HEAT: 2.0}, {**condenser_delta(4.0), HEAT: 1.0}
    estimator = FlowEstimator(CONDENSER)
    for i in range(13):  # every 30 s through 360 s, alternating
        estimate = estimator.add(30 * i, high if i % 2 == 0 else low)
    ratio_of_sums = 60 * (2.0 + 1.0) / (1.00 * 4.18 * (2.0 + 4.0))  # equal halves of the window
    mean_of_ratios = (60 * 2.0 / (4.18 * 2.0) + 60 * 1.0 / (4.18 * 4.0)) / 2
    assert ratio_of_sums == pytest.approx(7.2, abs=0.05) and mean_of_ratios == pytest.approx(9.0, abs=0.05)
    assert estimate.source == "calculated"
    assert estimate.value == pytest.approx(ratio_of_sums, rel=0.01)
    assert abs(estimate.value - mean_of_ratios) > 0.10 * mean_of_ratios


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


SOURCES = {"calculated", "pump_speed", "unknown"}
WINDOW_RUN = 11  # STEADY samples 30 s apart: 300 s, one full default window of running


def assert_bounded(estimator: FlowEstimator, timeline) -> set[str]:
    """Feed ``timeline``, check every estimate is None or a finite float in 0 … MAXIMUM; returns the sources."""
    sources = set()
    for timestamp, data in timeline:
        estimate = estimator.add(timestamp, data)
        sources.add(estimate.source)
        assert estimate.source in SOURCES
        if estimate.value is None:
            assert estimate.source == "unknown"
        else:
            assert type(estimate.value) is float and math.isfinite(estimate.value)
            assert 0.0 <= estimate.value <= MAXIMUM
    return sources


def steady_run(start: float) -> list[tuple[float, dict]]:
    return [(start + 30.0 * i, STEADY) for i in range(WINDOW_RUN)]


def fixed_timeline() -> list[tuple[float, dict]]:
    """Each hostile sample arrives inside a full window of running samples and is followed by another."""
    timeline = []
    for data in hostile_samples():
        timeline += steady_run(len(timeline) * 30.0)
        timeline.append((len(timeline) * 30.0, data))
    timeline += steady_run(len(timeline) * 30.0)
    return timeline


def clock_timeline(start: float) -> list[tuple[float, dict]]:
    """Clock steps and malformed updates, kept apart from the hostile samples."""
    t = start
    return [(t, STEADY), (t - 60.0, STEADY), (math.nan, STEADY), (math.inf, STEADY), (None, STEADY),
            ("later", STEADY), (t + 30.0, None), (t + 60.0, "not a dict"), (t + 90.0, {})]


def random_timeline(seed: int, length: int = 1500) -> list[tuple[float, dict]]:
    """Seeded mix of hostile samples, clock steps and full running windows, at least ``length`` samples."""
    rng = random.Random(seed)
    pool = hostile_samples() + [condenser_delta(2.1)] * 5
    t, timeline = 0.0, []
    while len(timeline) < length:
        if rng.random() < 0.1:  # a full window of running, so hostile samples land inside one
            timeline += steady_run(t + 30.0)
            t = timeline[-1][0]
            continue
        t += rng.choice((30.0, 30.0, 30.0, 0.0, -30.0, 1e-9, 600.0))
        timeline.append((t, rng.choice(pool)))
    return timeline


# DHF-REQ: genesis-wp/requirement-31  DHF-TEST: genesis-wp/ac-118
@pytest.mark.parametrize("loop", [CONDENSER, BRINE], ids=lambda loop: loop.name)
def test_no_input_sequence_makes_the_estimate_non_finite_negative_or_above_the_maximum(loop):
    estimator, fixed = FlowEstimator(loop, max_flow=MAXIMUM), fixed_timeline()
    assert assert_bounded(estimator, fixed) == SOURCES
    assert_bounded(estimator, clock_timeline(fixed[-1][0]))
    for seed in (1, 2, 3):
        timeline = random_timeline(seed)
        assert len(timeline) >= 1000
        assert assert_bounded(FlowEstimator(loop, max_flow=MAXIMUM), timeline) == SOURCES


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
    {"max_flow": 0}, {"fluid": "water"}, {"max_gap_s": 0}, {"max_gap_s": math.nan},
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


# ---- formal_review-11: in-area edge cases (B1-B4, B6) ----


def test_an_oversized_integer_is_untrusted_data_not_an_overflow():
    estimator = FlowEstimator(CONDENSER)
    hostile = ({**STEADY, HEAT: 10**400}, {**STEADY, CONDENSER_PUMP: 10**400}, {**STEADY, SPEED: -10**400})
    for t, data in enumerate(hostile):
        estimate = estimator.add(30 * t, data)
        assert estimate.value is None and estimate.source == "unknown"
    assert estimator.add(10**400, STEADY).source == "unknown"
    with pytest.raises(ValueError):
        FlowEstimator(CONDENSER, window_s=10**400)


def test_a_gap_between_running_samples_restarts_the_window():
    gap = FlowEstimator(CONDENSER)
    gap.add(0, STEADY)
    assert gap.add(3600, STEADY).source == "unknown"  # never observed running in between
    short = FlowEstimator(CONDENSER)
    feed(short, STEADY, seconds=150)
    assert feed(short, STEADY, start=600, seconds=210).source == "unknown"  # only 210 s since the gap
    assert gap.calibration is None and short.calibration is None
    tolerated = FlowEstimator(CONDENSER, max_gap_s=200)
    for t in (0, 150, 300):  # each gap within 200 s, but more than one interval per window
        estimate = tolerated.add(t, STEADY)
    assert estimate.source == "calculated"


# DHF-REQ: genesis-wp/requirement-31  DHF-TEST: genesis-wp/ac-132
@pytest.mark.parametrize("max_gap_s", [300, 600])
def test_constructor_rejects_a_maximum_gap_not_shorter_than_the_window(max_gap_s):
    with pytest.raises(ValueError, match="max_gap_s.*window_s"):
        FlowEstimator(CONDENSER, window_s=300, max_gap_s=max_gap_s)


def test_constructor_accepts_a_maximum_gap_shorter_than_the_window():
    estimator = FlowEstimator(CONDENSER, window_s=300, max_gap_s=299)
    assert (estimator.window_s, estimator.max_gap_s) == (300.0, 299.0)


def skipped_factors(caplog) -> list[float]:
    """Factors [L/min per %] named by log records of a skipped calibration."""
    factors = []
    for record in caplog.records:
        message = record.getMessage()
        if "calibration" in message and "skipped" in message:
            factors += [float(number) for number in re.findall(r"factor (\d+(?:\.\d+)?)", message)]
    return factors


# DHF-REQ: genesis-wp/requirement-31  DHF-TEST: genesis-wp/ac-129
def test_an_implausible_calibration_window_leaves_the_calibration_unchanged(caplog):
    caplog.set_level(logging.DEBUG, logger="genesis_wp.derived")
    low_pump = {**STEADY, CONDENSER_PUMP: 2.0}  # 7.6 L/min over 2 %: 3.8 L/min per %, × 100 % > 60 L/min
    implausible = CONDENSER_STEADY / 2.0

    first = FlowEstimator(CONDENSER)
    assert feed(first, low_pump).source == "calculated"
    assert first.calibration is None
    assert any(factor == pytest.approx(implausible, rel=0.01) for factor in skipped_factors(caplog))
    resting = first.add(400, {**STOPPED, CONDENSER_PUMP: 35.0})
    assert resting.source == "unknown" and resting.value is None

    caplog.clear()
    second = FlowEstimator(CONDENSER)
    feed(second, STEADY)  # a plausible window at 35 %
    earlier = second.calibration
    assert earlier == pytest.approx(CONDENSER_STEADY / 35, rel=1e-9)
    # Allow the next once-per-window check after the transition window was rejected.
    assert feed(second, low_pump, start=390, seconds=600).source == "calculated"
    assert second.calibration == earlier
    assert any(factor == pytest.approx(implausible, rel=0.01) for factor in skipped_factors(caplog))
    resting = second.add(1020, {**STOPPED, CONDENSER_PUMP: 35.0})
    assert resting.source == "pump_speed" and resting.value == pytest.approx(earlier * 35, rel=1e-9)


def test_a_running_sample_without_a_positive_temperature_difference_fails_closed():
    estimator = FlowEstimator(CONDENSER)
    feed(estimator, STEADY, seconds=300)
    for t, delta in ((330, -5.0), (360, 0.0)):
        estimate = estimator.add(t, condenser_delta(delta))
        assert estimate.source == "pump_speed"
        assert estimate.value == pytest.approx(CONDENSER_STEADY, rel=0.01)
    assert feed(estimator, STEADY, start=390, seconds=270).source == "pump_speed"  # the window restarted


def test_calibration_pairs_the_window_flow_with_the_window_pump_speed():
    estimator = FlowEstimator(CONDENSER)
    feed(estimator, STEADY, seconds=330)
    estimator.add(360, {**STEADY, CONDENSER_PUMP: 50.0})  # last sample at another pump speed
    resting = estimator.add(390, {**STOPPED, CONDENSER_PUMP: 35.0})
    assert resting.source == "pump_speed" and resting.value == pytest.approx(CONDENSER_STEADY, rel=0.01)


@pytest.mark.parametrize("every", [10, 30, 60])
def test_calibration_does_not_depend_on_the_poll_rate(every):
    estimator = FlowEstimator(CONDENSER)
    feed(estimator, STEADY, seconds=600, every=every)
    first = estimator.calibration
    feed(estimator, {**STEADY, HEAT: 2 * 1.73}, start=600 + every, seconds=1200 - every, every=every)
    # Non-overlapping windows ending near 900, 1200, 1500 and 1800 s, each weighted 0.1.
    assert first == pytest.approx(CONDENSER_STEADY / 35, rel=1e-9)
    target = 2 * CONDENSER_STEADY / 35
    assert estimator.calibration == pytest.approx(target + (first - target) * 0.9 ** 4, rel=0.03)


def test_a_backward_clock_step_restarts_the_window_instead_of_freezing_the_estimate():
    estimator = FlowEstimator(CONDENSER)
    assert feed(estimator, STEADY).source == "calculated"
    stepped = estimator.add(360 - 3600, STEADY)
    assert stepped.source == "pump_speed"  # not the stale calculated value
    assert feed(estimator, STEADY, start=-3210, seconds=300).source == "calculated"
    repeated = estimator.add(-2910, STOPPED)  # same timestamp as the last sample: dropped
    assert repeated.source == "calculated"


# formal_review-12 B1: the product rho * cp must itself be a positive finite number.
@pytest.mark.parametrize("cp, density", [(1e-200, 1e-200), (1e200, 1e200)])
def test_fluid_rejects_an_underflowing_or_overflowing_volumetric_heat_capacity(cp, density):
    with pytest.raises(ValueError):
        Fluid(cp=cp, density=density, source="x")


def plausibility_skips(caplog):
    return [r for r in caplog.records if "calibration skipped, factor" in r.getMessage()]


# DHF-TEST: genesis-wp/requirement-31 DHF-TEST: genesis-wp/ac-150
def test_implausible_calibration_checks_wait_one_window(caplog):
    caplog.set_level(logging.DEBUG, logger="genesis_wp.derived")
    estimator = FlowEstimator(CONDENSER)
    timestamps = []
    for timestamp in range(0, 931, 30):
        before = len(plausibility_skips(caplog))
        estimate = estimator.add(timestamp, {**STEADY, CONDENSER_PUMP: 2.0})
        if len(plausibility_skips(caplog)) > before:
            timestamps.append(timestamp)
        assert estimate.calibration is None
    assert len(timestamps) >= 3
    assert all(b - a >= 300 for a, b in zip(timestamps, timestamps[1:]))


# DHF-TEST: genesis-wp/requirement-31 DHF-TEST: genesis-wp/ac-148
@pytest.mark.parametrize("precalibrated", [False, True])
def test_implausible_calibration_warns_once_per_run(caplog, precalibrated):
    caplog.set_level(logging.DEBUG, logger="genesis_wp.derived")
    estimator = FlowEstimator(CONDENSER)
    start = 0
    if precalibrated:
        feed(estimator, STEADY, seconds=300)
        assert estimator.calibration is not None
        start = 330
    caplog.clear()
    feed(estimator, {**STEADY, CONDENSER_PUMP: 2.0}, start=start, seconds=1200)
    records = plausibility_skips(caplog)
    assert len(records) >= 3
    assert records[0].levelno == logging.WARNING
    assert all(r.levelno == logging.DEBUG for r in records[1:])
    assert "60.0" in records[0].getMessage()
    assert skipped_factors(caplog)[0] > 0.6

    # A successful calibration ends the run; the next rejection warns again.
    feed(estimator, STEADY, start=start + 1230, seconds=600)
    assert estimator.calibration is not None
    caplog.clear()
    feed(estimator, {**STEADY, CONDENSER_PUMP: 2.0}, start=start + 1860, seconds=1200)
    records = plausibility_skips(caplog)
    assert len(records) >= 3
    assert records[0].levelno == logging.WARNING
    assert all(r.levelno == logging.DEBUG for r in records[1:])
