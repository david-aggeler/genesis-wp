"""Emulator heating model: set points from the heating curves (requirement-15)."""

from __future__ import annotations

import pytest

from genesis_wp import ThermiaGenesis
from genesis_wp.emulator.heating import interpolate

from ..harness import PROFILE_615

HC1_NOW = (28, 33, 35, 38, 40, 45, 49)  # holding 6-12 after the owner's HC1 step (formal_review-1)
HC2_NOW = (23, 28, 30, 33, 35, 40, 44)  # holding 109-115
NAMES = ["input_outdoor_temperature", "input_system_supply_line_calculated_set_point",
         "input_desired_temperature_distribution_circuit_mix_valve_1", "input_system_supply_line_temperature"]


def curves(hc1=HC1_NOW, hc2=HC2_NOW) -> dict:
    overrides = {("holding_registers", 6 + i): round(v * 100) for i, v in enumerate(hc1)}
    overrides.update({("holding_registers", 109 + i): round(v * 100) for i, v in enumerate(hc2)})
    return overrides


async def read(emu, client=None):
    own = client is None
    client = client or ThermiaGenesis(emu.host, port=emu.port, kind=emu.profile.model, delay=0)
    try:
        return await client.async_update(only_registers=NAMES)
    finally:
        if own:
            await client.close()


async def dynamic(start_emulator, outdoor, **options):
    return await start_emulator(PROFILE_615, dynamics=True, outdoor_temperature=outdoor, **options)


def test_interpolation_is_linear_and_flat_beyond_the_ends():
    assert interpolate(HC1_NOW, 8.75) == pytest.approx(33.5)
    assert interpolate(HC1_NOW, 17) == 28 and interpolate(HC1_NOW, 30) == 28
    assert interpolate(HC1_NOW, -30) == 49
    assert interpolate(HC1_NOW, -10) == pytest.approx(42.5)


# DHF-REQ: genesis-wp/requirement-15  DHF-TEST: genesis-wp/ac-48
async def test_recorded_2026_10_25_case(start_emulator):
    emu = await dynamic(start_emulator, 8.75, overrides=curves())
    emu.step()
    data = await read(emu)
    assert data["input_outdoor_temperature"] == 8.75
    assert data["input_system_supply_line_calculated_set_point"] == pytest.approx(33.5, abs=0.01)
    assert data["input_desired_temperature_distribution_circuit_mix_valve_1"] == pytest.approx(28.5, abs=0.01)


# DHF-REQ: genesis-wp/requirement-15  DHF-TEST: genesis-wp/ac-49
async def test_recorded_2026_10_22_case(start_emulator):
    emu = await dynamic(start_emulator, 7.45)  # the 6.1.5 profile's own curves: both 23 ... 44
    emu.step()
    data = await read(emu)
    assert data["input_system_supply_line_calculated_set_point"] == pytest.approx(29.02, abs=0.01)
    assert data["input_desired_temperature_distribution_circuit_mix_valve_1"] == pytest.approx(29.02, abs=0.01)


# DHF-REQ: genesis-wp/requirement-15  DHF-TEST: genesis-wp/ac-50
async def test_set_point_is_clamped_to_the_maximum_limit(start_emulator):
    emu = await dynamic(start_emulator, -25, overrides={**curves(), ("holding_registers", 3): 4500})
    emu.step()
    assert (await read(emu))["input_system_supply_line_calculated_set_point"] == 45


# DHF-REQ: genesis-wp/requirement-15  DHF-TEST: genesis-wp/ac-51
async def test_curve_point_written_to_the_emulator_moves_the_set_point(start_emulator):
    emu = await dynamic(start_emulator, 0)
    assert emu.profile.blocks["holding_registers"].value(9) == 3300
    emu.step()
    assert (await read(emu))["input_system_supply_line_calculated_set_point"] == 33
    writer = ThermiaGenesis(emu.host, port=emu.port, kind=emu.profile.model, delay=0, read_only=False)
    try:
        await writer.async_set("holding_set_point_heat_curve_y_4", 38.0)  # emulator only, never the real plant
    finally:
        await writer.close()
    emu.step()
    assert (await read(emu))["input_system_supply_line_calculated_set_point"] == 38


async def test_ignored_writes_leave_the_curve_alone(start_emulator):
    emu = await dynamic(start_emulator, 0, apply_writes=False)
    writer = ThermiaGenesis(emu.host, port=emu.port, kind=emu.profile.model, delay=0, read_only=False)
    try:
        await writer.async_set("holding_set_point_heat_curve_y_4", 38.0)
    finally:
        await writer.close()
    emu.step()
    assert (await read(emu))["input_system_supply_line_calculated_set_point"] == 33


# DHF-REQ: genesis-wp/requirement-15  DHF-TEST: genesis-wp/ac-52
async def test_supply_temperature_approaches_its_set_point_with_a_lag(start_emulator):
    emu = await dynamic(start_emulator, 8.75, overrides={**curves(), ("input_registers", 12): 2830})
    client = ThermiaGenesis(emu.host, port=emu.port, kind=emu.profile.model, delay=0)
    try:
        before = await read(emu, client)
        assert before["input_system_supply_line_temperature"] == 28.3
        assert before["input_system_supply_line_calculated_set_point"] == pytest.approx(33.5, abs=0.01)
        gap = 33.5 - 28.3
        for step in range(10):
            emu.step()
            data = await read(emu, client)
            t33, set_point = data["input_system_supply_line_temperature"], data["input_system_supply_line_calculated_set_point"]
            assert t33 <= set_point
            assert abs(set_point - t33) < gap
            if step == 0:
                assert t33 < set_point
            gap = abs(set_point - t33)
    finally:
        await client.close()


async def test_mixing_valve_open_while_buffer_below_hc2_set_point(start_emulator):
    emu = await dynamic(start_emulator, 8.75, overrides=curves())
    names = ["input_buffer_tank_temperature", "input_mix_valve_1_position", "input_mix_valve_1_supply_line_temperature"]
    client = ThermiaGenesis(emu.host, port=emu.port, kind=emu.profile.model, delay=0)
    try:
        emu.step()
        cold = await client.async_update(only_registers=names)
        emu.step(80)  # HC1 at 33.5 charges the buffer past the HC2 set point 28.5
        warm = await client.async_update(only_registers=names)
    finally:
        await client.close()
    assert cold["input_buffer_tank_temperature"] < 28.5
    assert cold["input_mix_valve_1_position"] == 100
    assert warm["input_buffer_tank_temperature"] > 28.5
    assert warm["input_mix_valve_1_position"] < 100
    assert warm["input_mix_valve_1_supply_line_temperature"] == pytest.approx(28.5, abs=0.05)


async def test_outdoor_temperature_drifts_slowly_when_not_held(start_emulator):
    emu = await start_emulator(PROFILE_615, dynamics=True)
    start = (await read(emu))["input_outdoor_temperature"]
    emu.step(51)  # odd: a ±0.02 K walk cannot be back at its start
    now = (await read(emu))["input_outdoor_temperature"]
    assert now != start and abs(now - start) <= 3.0


async def test_without_dynamics_set_points_are_the_captured_values(start_emulator):
    emu = await start_emulator(PROFILE_615)
    assert emu.heating is None
    assert (await read(emu))["input_system_supply_line_calculated_set_point"] == 29.02
