"""'Sensor not fitted' sentinels decode to None where a source declares them (requirement-3)."""

from __future__ import annotations

import pytest

from genesis_wp import ThermiaGenesis
from genesis_wp.const import KEY_NOT_FITTED, MODEL_MEGA, MODEL_STIEBEL_WPE_I_071, REGISTERS

from .harness import PROFILE_606, PROFILE_615, WPE_I_KIND, emulator_client

pytestmark = pytest.mark.fixture


async def update(emu, *registers):
    client = emulator_client(emu.host, port=emu.port, kind=WPE_I_KIND, delay=0)
    try:
        return await client.async_update(only_registers=list(registers))
    finally:
        await client.close()


# DHF-REQ: genesis-wp/requirement-3  DHF-TEST: genesis-wp/ac-7
@pytest.mark.parametrize("profile", [PROFILE_606, PROFILE_615])
async def test_scale_100_sentinel_20000_decodes_to_none(start_emulator, profile):
    emu = await start_emulator(profile)
    assert emu.profile.blocks["input_registers"].value(15) == 20000  # T36 not fitted on the unit
    data = await update(emu, "input_tap_water_top_temperature")
    assert data["input_tap_water_top_temperature"] is None


# DHF-REQ: genesis-wp/requirement-3  DHF-TEST: genesis-wp/ac-8
async def test_scale_10_sentinel_2000_decodes_to_none(start_emulator):
    emu = await start_emulator(PROFILE_615)
    assert emu.profile.blocks["input_registers"].value(121) == 2000  # room sensor T34 not fitted
    data = await update(emu, "input_room_temperature_sensor")
    assert data["input_room_temperature_sensor"] is None


# DHF-REQ: genesis-wp/requirement-3  DHF-TEST: genesis-wp/ac-9
async def test_register_without_declared_sentinel_keeps_200(start_emulator):
    # No capture holds an undeclared 20000 since input 45 declares one, so the emulator serves
    # raw 20000 on the discharge pipe (input 7), which declares none.
    assert KEY_NOT_FITTED not in REGISTERS["input_discharge_pipe_temperature"]
    emu = await start_emulator(PROFILE_615, overrides={("input_registers", 7): 20000})
    data = await update(emu, "input_discharge_pipe_temperature")
    assert data["input_discharge_pipe_temperature"] == 200.0


# Source correction, owner decision 2026-11-19 (issue-4): the plant has no hot gas recovery.
@pytest.mark.parametrize("profile", [PROFILE_606, PROFILE_615])
async def test_hot_gas_supply_sentinel_20000_decodes_to_none(start_emulator, profile):
    emu = await start_emulator(profile)
    assert emu.profile.blocks["input_registers"].value(45) == 20000
    data = await update(emu, "input_hgw_supply_line_temperature")
    assert data["input_hgw_supply_line_temperature"] is None


async def test_declared_register_with_a_real_reading_keeps_its_value(start_emulator):
    emu = await start_emulator(PROFILE_615)
    data = await update(emu, "input_system_supply_line_temperature")  # T33 is fitted
    assert data["input_system_supply_line_temperature"] == emu.profile.blocks["input_registers"].value(12) / 100


async def test_other_models_are_not_affected(mock_modbus_unit):
    """Sentinels are declared per model: a Mega reading raw 20000 still reports 200.0."""
    mock_modbus_unit.load_raw({"input": {15: 20000}})
    client = ThermiaGenesis(unit=mock_modbus_unit, kind=MODEL_MEGA, delay=0)
    data = await client.async_update(only_registers=["input_tap_water_top_temperature"])
    assert data["input_tap_water_top_temperature"] == 200.0


def test_declarations_name_only_the_wpe_i_model():
    declared = {name: meta[KEY_NOT_FITTED] for name, meta in REGISTERS.items() if KEY_NOT_FITTED in meta}
    assert declared, "no sentinel declarations"
    for name, per_model in declared.items():
        assert set(per_model) == {MODEL_STIEBEL_WPE_I_071}, name


# Measured correction (issue-2, owner-confirmed 2026-11-24): every read of the plant shows raw 0
# on input 68 while the buffer moves, so 0 marks "not used".
# DHF-REQ: genesis-wp/requirement-3  DHF-TEST: genesis-wp/ac-211
@pytest.mark.parametrize("profile", [PROFILE_606, PROFILE_615])
async def test_buffer_charge_set_point_zero_decodes_to_none(start_emulator, profile):
    emu = await start_emulator(profile)
    assert emu.profile.blocks["input_registers"].value(68) == 0
    data = await update(emu, "input_buffer_tank_charge_set_point")
    assert data["input_buffer_tank_charge_set_point"] is None


async def test_buffer_charge_set_point_nonzero_keeps_its_value(start_emulator):
    emu = await start_emulator(PROFILE_615, overrides={("input_registers", 68): 3000})
    data = await update(emu, "input_buffer_tank_charge_set_point")
    assert data["input_buffer_tank_charge_set_point"] == 30.0
