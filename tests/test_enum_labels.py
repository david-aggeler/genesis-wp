"""SG Ready mode and smart-grid state decode to documented labels (requirement-16)."""

from __future__ import annotations

import logging

import pytest

from genesis_wp import ThermiaGenesis

from .harness import PROFILE_615

pytestmark = pytest.mark.fixture

SG_REGISTERS = ["dinput_smart_grid_1", "dinput_smart_grid_2", "holding_power_consumption_control", "input_comfort_mode"]


async def update(emu) -> ThermiaGenesis:
    client = ThermiaGenesis(emu.host, port=emu.port, kind=emu.profile.model, delay=0)
    try:
        await client.async_update(only_registers=SG_REGISTERS)
    finally:
        await client.close()
    return client


# DHF-REQ: genesis-wp/requirement-16  DHF-TEST: genesis-wp/ac-53
@pytest.mark.parametrize("raw, label", [(0, "normal"), (1, "load_up"), (2, "utility_block"), (3, "maximum")])
async def test_sg_ready_mode_labels(start_emulator, raw, label):
    emu = await start_emulator(PROFILE_615, overrides={("holding_registers", 124): raw})
    client = await update(emu)
    assert client.data["holding_power_consumption_control"] == raw  # data keeps the raw value
    value = client.labels["holding_power_consumption_control"]
    assert (value.raw, value.label, value.labeled) == (raw, label, True)


# DHF-REQ: genesis-wp/requirement-16  DHF-TEST: genesis-wp/ac-54
@pytest.mark.parametrize("raw, label", [(1, "utility_block"), (4, "normal"), (5, "comfort"), (6, "maximum")])
async def test_smart_grid_state_labels(start_emulator, raw, label):
    emu = await start_emulator(PROFILE_615, overrides={("input_registers", 82): raw})
    client = await update(emu)
    assert client.labels["input_comfort_mode"].label == label


# DHF-REQ: genesis-wp/requirement-16  DHF-TEST: genesis-wp/ac-55
async def test_undocumented_value_is_raw_and_flagged(start_emulator, caplog):
    emu = await start_emulator(PROFILE_615, overrides={("input_registers", 82): 3})
    with caplog.at_level(logging.WARNING, logger="genesis_wp"):
        client = await update(emu)
    value = client.labels["input_comfort_mode"]
    assert (value.raw, value.label, value.labeled) == (3, None, False)
    assert client.data["input_comfort_mode"] == 3
    assert "no documented label" in caplog.text


async def test_smart_grid_inputs_are_reported(start_emulator):
    emu = await start_emulator(PROFILE_615, overrides={("discrete_inputs", 204): 1})
    client = await update(emu)
    assert not client.data["dinput_smart_grid_1"]
    assert client.data["dinput_smart_grid_2"]
