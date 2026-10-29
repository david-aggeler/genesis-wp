"""Demand registers decode the Genesis 17 values and never coerce an unknown one (requirement-17)."""

from __future__ import annotations

import logging

import pytest

from genesis_wp import ThermiaGenesis
from genesis_wp.const import MODEL_MEGA

from .harness import PROFILE_615

pytestmark = pytest.mark.fixture

DEMAND = "input_first_prioritised_demand"


async def update(emu) -> ThermiaGenesis:
    client = ThermiaGenesis(emu.host, port=emu.port, kind=emu.profile.model, delay=0)
    try:
        await client.async_update(only_registers=[DEMAND, "input_second_prioritised_demand"])
    finally:
        await client.close()
    return client


# DHF-REQ: genesis-wp/requirement-17  DHF-TEST: genesis-wp/ac-57
@pytest.mark.parametrize("raw, label", [(8, "Passive cooling"), (100, "OFF")])
async def test_passive_cooling_and_off_decode(start_emulator, raw, label):
    emu = await start_emulator(PROFILE_615, overrides={("input_registers", 1): raw})
    client = await update(emu)
    assert client.data[DEMAND] == label
    assert (client.labels[DEMAND].raw, client.labels[DEMAND].label) == (raw, label)


# DHF-REQ: genesis-wp/requirement-17  DHF-TEST: genesis-wp/ac-58
async def test_undocumented_value_stays_raw(start_emulator, caplog):
    emu = await start_emulator(PROFILE_615, overrides={("input_registers", 1): 42})
    with caplog.at_level(logging.WARNING, logger="genesis_wp"):
        client = await update(emu)
    assert client.data[DEMAND] == 42
    assert (client.labels[DEMAND].raw, client.labels[DEMAND].label) == (42, None)
    assert DEMAND in caplog.text and "no documented label" in caplog.text


async def test_captured_values_keep_their_strings(start_emulator):
    client = await update(await start_emulator(PROFILE_615))
    assert client.data[DEMAND] == "Heat"  # raw 4
    assert client.data["input_second_prioritised_demand"] == "No demand"  # raw 99
    assert client.labels[DEMAND].raw == 4


async def test_legacy_models_keep_the_upstream_table(mock_modbus_unit):
    """No document for mega / inverter: their upstream decoding is unchanged."""
    mock_modbus_unit.load_raw({"input": {1: 42}})
    client = ThermiaGenesis(unit=mock_modbus_unit, kind=MODEL_MEGA, delay=0)
    data = await client.async_update(only_registers=[DEMAND])
    assert data[DEMAND] == "OFF"
    assert DEMAND not in client.labels


@pytest.mark.parametrize("raw, label", [
    (0, "OFF"), (1, "Manual Operation"), (2, "Defrost"), (3, "Hot water"), (4, "Heat"), (5, "Cool"),
    (6, "Pool"), (7, "Anti legionella"), (8, "OFF"), (98, "Standby"), (99, "No demand"), (100, "OFF"),
])
async def test_legacy_status_table(mock_modbus_unit, raw, label):
    mock_modbus_unit.load_raw({"input": {1: raw}})
    client = ThermiaGenesis(unit=mock_modbus_unit, kind=MODEL_MEGA, delay=0)
    assert (await client.async_update(only_registers=[DEMAND]))[DEMAND] == label
