"""A register valid only while a selector holds a documented value reads None otherwise (requirement-29)."""

from __future__ import annotations

import pytest
from modbus_connection import ModbusExceptionError

from genesis_wp import ThermiaGenesis
from genesis_wp.const import KEY_VALID_WHEN, MODEL_STIEBEL_WPE_I_071, REGISTERS

from .harness import PROFILE_606, PROFILE_615, WPE_I_KIND, emulator_client

pytestmark = pytest.mark.fixture

SENSOR = "holding_outdoor_temperature_sensor"  # holding 118, BMS outdoor temperature input
SOURCE = "holding_outdoor_temperature_source"  # holding 117, outdoor temperature source select


async def update(emu, registers=None):
    client = emulator_client(emu.host, port=emu.port, kind=WPE_I_KIND, delay=0)
    try:
        return await client.async_update(only_registers=registers)
    finally:
        await client.close()


# DHF-REQ: genesis-wp/requirement-29  DHF-TEST: genesis-wp/ac-111
@pytest.mark.parametrize("profile", [PROFILE_606, PROFILE_615])
async def test_sensor_reads_none_while_the_pt1000_sensor_is_selected(start_emulator, profile):
    emu = await start_emulator(profile)
    assert emu.profile.blocks["holding_registers"].value(117) == 0
    assert emu.profile.blocks["holding_registers"].value(118) == 20000
    data = await update(emu)
    assert SENSOR in data
    assert data[SENSOR] is None


# DHF-REQ: genesis-wp/requirement-29  DHF-TEST: genesis-wp/ac-112
async def test_sensor_reports_its_scaled_value_while_the_bms_is_selected(start_emulator):
    emu = await start_emulator(PROFILE_615, overrides={("holding_registers", 117): 1, ("holding_registers", 118): 735})
    data = await update(emu)
    assert data[SENSOR] == 7.35


# DHF-REQ: genesis-wp/requirement-29  DHF-TEST: genesis-wp/ac-113
async def test_sensor_reads_none_when_the_selector_is_not_in_the_update(start_emulator):
    emu = await start_emulator(PROFILE_615, overrides={("holding_registers", 117): 1, ("holding_registers", 118): 735})
    data = await update(emu, [SENSOR])
    assert SOURCE not in data
    assert data[SENSOR] is None


async def test_selector_value_other_than_the_required_one_reads_none(start_emulator):
    emu = await start_emulator(PROFILE_615, overrides={("holding_registers", 117): 2, ("holding_registers", 118): 735})
    data = await update(emu, [SOURCE, SENSOR])
    assert data[SOURCE] == 2
    assert data[SENSOR] is None


async def test_unread_selector_reads_none(mock_modbus_unit):
    """A selector whose chunk the device refused is unknown: fail closed."""
    mock_modbus_unit.load_raw({"holding": {117: 1, 118: 735}})
    mock_modbus_unit.fail_read(117, ModbusExceptionError(2), register_type="holding")
    client = ThermiaGenesis(unit=mock_modbus_unit, kind=WPE_I_KIND, delay=0, max_registers=1)
    data = await client.async_update(only_registers=[SOURCE, SENSOR])
    assert SOURCE in client.unread
    assert data[SENSOR] is None


async def test_other_models_are_not_affected(mock_modbus_unit):
    """The condition is declared per model: a Calibra RXT keeps reporting the number."""
    mock_modbus_unit.load_raw({"holding": {117: 0, 118: 20000}})
    client = ThermiaGenesis(unit=mock_modbus_unit, kind="calibra_rxt", delay=0)
    data = await client.async_update(only_registers=[SOURCE, SENSOR])
    assert data[SENSOR] == 200.0


def test_declarations_name_only_the_wpe_i_model():
    declared = {name: meta[KEY_VALID_WHEN] for name, meta in REGISTERS.items() if KEY_VALID_WHEN in meta}
    assert declared == {SENSOR: {MODEL_STIEBEL_WPE_I_071: (SOURCE, 1)}}
