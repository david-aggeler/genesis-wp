"""Heating circuit 2 mixer leg flows from the mixing energy balance (requirement-51).

Hermetic: the estimator takes update dicts and a condenser flow, and needs no device and no emulator.
"""

from __future__ import annotations

import logging
import math

import pytest

from tests.test_derived_return import (
    BASE, CIRCUIT_FLOW, COMPRESSOR, CONDENSER_FLOW, Q_CONDENSER, SLOPE, T8, T9, T32, T33, VHC, VOLUME,
    estimator, expected, warming,
)

pytestmark = pytest.mark.unit

CONDENSER_PUMP = "dinput_condenser_pump_on_off_control"  # discrete input 210
LEGS = ("hot_port_flow", "bypass_flow", "buffer_return_flow", "heat_pump_return_flow", "either_way_flow")


def feed(est, samples, flows):
    estimate = None
    for i, (data, flow) in enumerate(zip(samples, flows)):
        estimate = est.add(60.0 * i, data, flow)
    return estimate


def window(slope: float, data: dict = BASE, count: int = 31) -> list[dict]:
    return [warming(60.0 * i, slope, data) for i in range(count)]


def t33_mean(samples: list[dict]) -> float:
    """Time-weighted mean T33 at 60 s steps: each interval carries its closing sample."""
    return sum(s[T33] for s in samples[1:]) / (len(samples) - 1)


def legs(estimate) -> dict:
    return {name: getattr(estimate, name) for name in LEGS}


# DHF-REQ: genesis-wp/requirement-51  DHF-TEST: genesis-wp/ac-202
def test_leg_flows_follow_the_mixing_and_mass_balance():
    samples = window(SLOPE)
    estimate = feed(estimator(), samples, [CONDENSER_FLOW] * len(samples))
    heat = Q_CONDENSER - VOLUME * VHC * SLOPE
    t_return = expected(heat)
    hot = 60.0 * heat / (VHC * (t33_mean(samples) - t_return))
    assert estimate.value == pytest.approx(t_return, abs=1e-6)
    assert estimate.hot_port_flow == pytest.approx(hot, abs=1e-6)
    assert estimate.bypass_flow == pytest.approx(CIRCUIT_FLOW - hot, abs=1e-6)
    assert estimate.buffer_return_flow == pytest.approx(hot, abs=1e-6)
    assert estimate.heat_pump_return_flow == pytest.approx(CONDENSER_FLOW, abs=1e-6)
    assert estimate.either_way_flow == pytest.approx(CONDENSER_FLOW - hot, abs=1e-6)
    assert 0 < hot < CIRCUIT_FLOW


# DHF-REQ: genesis-wp/requirement-51  DHF-TEST: genesis-wp/ac-202
def test_either_way_flow_is_negative_when_the_buffer_feeds_the_mixer():
    """Compressor and condenser pump off: the buffer top supplies the whole hot-port flow."""
    stopped = {**BASE, COMPRESSOR: False, CONDENSER_PUMP: False, T8: None, T9: None, T33: 32.0}
    samples = window(-SLOPE, stopped)
    estimate = feed(estimator(), samples, [None] * len(samples))
    assert estimate.hot_port_flow > 0
    assert estimate.heat_pump_return_flow == 0.0
    assert estimate.either_way_flow == pytest.approx(-estimate.hot_port_flow, abs=1e-9)


# DHF-REQ: genesis-wp/requirement-51  DHF-TEST: genesis-wp/ac-203
def test_leg_flows_fail_closed_below_the_temperature_floor(caplog):
    samples = window(SLOPE)
    flows = [CONDENSER_FLOW] * len(samples)
    reference = feed(estimator(), samples, flows)
    gap = t33_mean(samples) - reference.value
    caplog.set_level(logging.DEBUG, logger="genesis_wp.derived")
    estimate = feed(estimator(min_mixer_delta_t=gap + 0.1), samples, flows)
    assert estimate.value == pytest.approx(reference.value)  # the return estimate itself is unaffected
    assert estimate.hot_port_flow is None and estimate.bypass_flow is None
    assert estimate.buffer_return_flow is None and estimate.either_way_flow is None
    assert "below the" in caplog.text and "floor" in caplog.text


# DHF-REQ: genesis-wp/requirement-51  DHF-TEST: genesis-wp/ac-203
def test_leg_flows_fail_closed_when_the_hot_port_exceeds_the_circuit_flow(caplog):
    """T33 below T32 cannot be a real mixer state: the hot share would exceed the circuit flow."""
    samples = window(SLOPE, {**BASE, T33: BASE[T32] - 1.0, "input_buffer_tank_temperature": 31.0})
    caplog.set_level(logging.DEBUG, logger="genesis_wp.derived")
    estimate = feed(estimator(min_mixer_delta_t=0.01), samples, [CONDENSER_FLOW] * len(samples))
    assert estimate.value is not None
    assert all(estimate.__getattribute__(name) is None for name in LEGS if name != "heat_pump_return_flow")
    assert "outside 0" in caplog.text


# DHF-REQ: genesis-wp/requirement-51  DHF-TEST: genesis-wp/ac-203
def test_leg_flows_are_none_without_a_return_estimate():
    samples = window(SLOPE, count=10)  # window not yet covered
    estimate = feed(estimator(), samples, [CONDENSER_FLOW] * len(samples))
    assert estimate.value is None
    assert all(value is None for value in legs(estimate).values())


# DHF-REQ: genesis-wp/requirement-51  DHF-TEST: genesis-wp/ac-203
@pytest.mark.parametrize("floor", [0, -1.0, math.nan, math.inf, "2"])
def test_an_invalid_temperature_floor_is_rejected(floor):
    with pytest.raises(ValueError, match="min_mixer_delta_t"):
        estimator(min_mixer_delta_t=floor)


# DHF-REQ: genesis-wp/requirement-51  DHF-TEST: genesis-wp/ac-204
@pytest.mark.parametrize("pump, flow, bottom", [
    (True, 5.0, 5.0),     # pump running, flow known: the estimate is used
    (False, None, 0.0),   # pump signal off: 0
    (True, None, None),   # pump running, flow unknown: None
])
def test_condenser_flow_uses_the_estimate_and_0_only_with_the_pump_off(pump, flow, bottom):
    stopped = {**BASE, COMPRESSOR: False, CONDENSER_PUMP: pump, T8: None, T9: None, T33: 32.0}
    samples = window(-SLOPE, stopped)
    estimate = feed(estimator(), samples, [flow] * len(samples))
    assert estimate.hot_port_flow is not None  # T33 32 °C keeps the gap above the 2 K floor
    reference = feed(estimator(), window(-SLOPE, {**stopped, CONDENSER_PUMP: False}), [None] * len(samples))
    assert estimate.hot_port_flow == pytest.approx(reference.hot_port_flow)
    assert estimate.bypass_flow == pytest.approx(reference.bypass_flow)
    if bottom is None:
        assert estimate.heat_pump_return_flow is None and estimate.either_way_flow is None
    else:
        assert estimate.heat_pump_return_flow == pytest.approx(bottom)
        assert estimate.either_way_flow == pytest.approx(bottom - estimate.hot_port_flow)
