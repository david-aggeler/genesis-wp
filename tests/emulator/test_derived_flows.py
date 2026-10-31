"""The emulator reproduces the derived flows: estimators fed by library updates (requirement-31)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from genesis_wp.derived import BRINE, BRINE_MEG25, CONDENSER, WATER, FlowEstimator
from genesis_wp.emulator import heating
from genesis_wp.emulator.climate import BUCKETS, LICENSE, SOURCE, ClimateProfile

from ..harness import PROFILE_615, library_full_update

pytestmark = pytest.mark.dynamics

START = datetime(2026, 1, 15, 0, 0)
CONDENSER_PUMP = "input_condenser_circulation_pump_speed"
BRINE_PUMP = "input_brine_circulation_pump_speed"
HEAT = "input_current_power_output"
POWER = "input_current_consumed_power"


def flat_climate(celsius: float = 5.0) -> ClimateProfile:
    values = tuple(tuple(celsius for _ in range(24)) for _ in range(BUCKETS["month"]))
    return ClimateProfile("month", date(2021, 1, 1), date(2025, 12, 31), 0.0, 0.0, "UTC", SOURCE, LICENSE, values)


def served_delta_t(data: dict) -> tuple[float, float]:
    return (data["input_condenser_out_temperature"] - data["input_condenser_in_temperature"],
            data["input_brine_in_temperature"] - data["input_brine_out_temperature"])


# DHF-REQ: genesis-wp/requirement-31  DHF-TEST: genesis-wp/ac-121
async def test_estimators_recover_the_emulators_configured_flows(start_emulator):
    emu = await start_emulator(PROFILE_615, dynamics=True, climate=flat_climate(), start_time=START)
    condenser, brine = FlowEstimator(CONDENSER), FlowEstimator(BRINE)
    for _ in range(8):  # 7 simulated minutes: more than one 300 s window
        data = await library_full_update(emu)
        assert data["input_compressor_speed_rpm"] > 0
        configured = (heating.CONDENSER_FLOW_PER_PERCENT * data[CONDENSER_PUMP],
                      heating.BRINE_FLOW_PER_PERCENT * data[BRINE_PUMP])
        # Every update serves inlet temperatures consistent with the configured flow, not drift.
        expected = (60 * data[HEAT] / (WATER.volumetric_heat_capacity * configured[0]),
                    60 * (data[HEAT] - data[POWER]) / (BRINE_MEG25.volumetric_heat_capacity * configured[1]))
        assert served_delta_t(data) == pytest.approx(expected, abs=0.03)
        timestamp = emu.simulated_time.timestamp()
        estimates = condenser.add(timestamp, data), brine.add(timestamp, data)
        emu.advance(60)
    for estimate, flow in zip(estimates, configured):
        assert estimate.source == "calculated"
        assert estimate.value == pytest.approx(flow, rel=0.05)


async def test_stopped_compressor_serves_inlet_equal_to_outlet(start_emulator):
    stopped = {("input_registers", heating.SPEED): 0, ("input_registers", heating.CONDENSER_PUMP): 0}
    emu = await start_emulator(PROFILE_615, dynamics=True, overrides=stopped)
    emu.step()
    data = await library_full_update(emu)
    assert data[HEAT] == 0.0
    assert served_delta_t(data) == (0.0, 0.0)


async def test_inlet_temperatures_are_served_as_captured_without_dynamics(start_emulator):
    emu = await start_emulator(PROFILE_615)
    data = await library_full_update(emu)
    inputs = emu.profile.blocks["input_registers"]
    assert data["input_condenser_in_temperature"] == inputs.value(heating.CONDENSER_IN) / 100
    assert data["input_brine_in_temperature"] == inputs.value(heating.BRINE_IN) / 100


async def test_running_compressor_without_pump_flow_serves_inlet_equal_to_outlet(start_emulator):
    no_flow = {("input_registers", heating.CONDENSER_PUMP): 0, ("input_registers", heating.BRINE_PUMP): 0}
    emu = await start_emulator(PROFILE_615, dynamics=True, overrides=no_flow)
    emu.step()
    data = await library_full_update(emu)
    assert data["input_compressor_speed_rpm"] > 0 and data[HEAT] > 0
    assert data[CONDENSER_PUMP] == data[BRINE_PUMP] == 0
    assert served_delta_t(data) == (0.0, 0.0)


async def test_a_tiny_pump_speed_serves_a_bounded_inlet_temperature_not_a_wrapped_one(start_emulator):
    tiny = {("input_registers", heating.CONDENSER_PUMP): 30, ("input_registers", heating.BRINE_PUMP): 30}  # 0.3 %
    emu = await start_emulator(PROFILE_615, dynamics=True, overrides=tiny)
    emu.step()
    data = await library_full_update(emu)
    assert data["input_compressor_speed_rpm"] > 0 and data[HEAT] > 0
    condenser, brine = served_delta_t(data)
    assert condenser == pytest.approx(heating.MAX_RISE, abs=0.01)
    assert brine == pytest.approx(heating.MAX_RISE, abs=0.01)
    assert data["input_condenser_in_temperature"] < data["input_condenser_out_temperature"]


# formal_review-12 B2: the served inlet never wraps the signed 16-bit register.
@pytest.mark.parametrize(("outlet", "inlet", "low"), [
    (heating.CONDENSER_OUT, "input_condenser_in_temperature", True),
    (heating.BRINE_OUT, "input_brine_in_temperature", False),
])
async def test_an_extreme_outlet_serves_a_clamped_inlet_not_a_wrapped_one(start_emulator, outlet, inlet, low):
    # Overrides are raw unsigned words: -320.00 °C is 0x10000 - 32000.
    extreme = {("input_registers", outlet): 0x10000 - 32000 if low else 32000,
               ("input_registers", heating.CONDENSER_PUMP): 50, ("input_registers", heating.BRINE_PUMP): 50}
    emu = await start_emulator(PROFILE_615, dynamics=True, overrides=extreme)
    data = await library_full_update(emu)
    assert data["input_compressor_speed_rpm"] > 0 and data[HEAT] > 0
    # Clamped at the range ends; the top is 327.66 because the library reads raw 32767 as 0.
    assert data[inlet] == (-327.68 if low else 327.66)
