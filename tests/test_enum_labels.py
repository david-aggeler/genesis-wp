"""Enumeration registers decode to their documented labels.

SG Ready mode and smart-grid state (requirement-16); every other WPE-I register a source types as
an enumeration (requirement-32).
"""

from __future__ import annotations

import logging

import pytest

from genesis_wp import ThermiaGenesis
from genesis_wp.const import KEY_ADDRESS, KEY_ENUM, KEY_REG_TYPE, MODEL_STIEBEL_WPE_I_071, REGISTERS

from .harness import PROFILE_615
from .register_sources import STIEBEL_WPE_I_071_ENUMS, THERMIA_GENESIS_17_1_ENUMS

pytestmark = pytest.mark.fixture

SG_REGISTERS = ["dinput_smart_grid_1", "dinput_smart_grid_2", "holding_power_consumption_control", "input_comfort_mode"]


async def update(emu, registers=SG_REGISTERS) -> ThermiaGenesis:
    client = ThermiaGenesis(emu.host, port=emu.port, kind=emu.profile.model, delay=0)
    try:
        await client.async_update(only_registers=registers)
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


# Requirement-32: the source tables, as {register: (emulator block, address, {raw: label})}.
# Stiebel doc 372257-47978-0071 sections 2.3, 2.18, 2.22 footnote *8; Thermia 17.1 footnotes *2, *7.
SOURCE_LABELS = {
    "holding_operational_mode": ("holding_registers", 0, {1: "off", 2: "standby", 3: "on_auto"}),
    "holding_outdoor_temperature_source": ("holding_registers", 117, {0: "sensor", 1: "bms"}),
    "holding_selected_mode_mixing_valve_1": ("holding_registers", 298, {0: "heat", 1: "cool", 2: "auto"}),
    "holding_enabled_immersion_heater": ("holding_registers", 321, {0: "disabled", 2: "internal"}),
    "input_ksm_fan_test_status": ("input_registers", 413, {
        -1: "no_test_recorded", 0: "not_initialized", 1: "running", 2: "passed", 3: "failed",
    }),
}


def _source_enum_addresses():
    blocks = set(STIEBEL_WPE_I_071_ENUMS) | set(THERMIA_GENESIS_17_1_ENUMS)
    return {
        (block, address)
        for block in blocks
        for address in STIEBEL_WPE_I_071_ENUMS.get(block, frozenset()) | THERMIA_GENESIS_17_1_ENUMS.get(block, frozenset())
    }


# DHF-REQ: genesis-wp/requirement-32  DHF-TEST: genesis-wp/ac-123
def test_every_source_enumeration_on_the_wpe_i_declares_labels():
    model = MODEL_STIEBEL_WPE_I_071
    addresses = _source_enum_addresses()
    checked, unlabeled = [], []
    for name, meta in REGISTERS.items():
        if not meta[model] or (meta[KEY_REG_TYPE], meta[KEY_ADDRESS]) not in addresses:
            continue
        checked.append(name)
        if not meta.get(KEY_ENUM, {}).get(model):
            unlabeled.append(name)
    assert unlabeled == [], f"source enumerations without labels: {unlabeled}"
    assert set(SOURCE_LABELS) <= set(checked)


# DHF-REQ: genesis-wp/requirement-32  DHF-TEST: genesis-wp/ac-124 genesis-wp/ac-127
@pytest.mark.parametrize(("name", "raw", "label"), [
    (name, raw, label) for name, (_, _, table) in SOURCE_LABELS.items() for raw, label in table.items()
])
async def test_source_enumerations_report_their_documented_labels(start_emulator, name, raw, label):
    block, address, _ = SOURCE_LABELS[name]
    emu = await start_emulator(PROFILE_615, overrides={(block, address): raw & 0xFFFF})
    client = await update(emu, [name])
    assert client.data[name] == raw  # data keeps the raw value
    value = client.labels[name]
    assert (value.raw, value.label, value.labeled) == (raw, label, True)


# DHF-REQ: genesis-wp/requirement-32  DHF-TEST: genesis-wp/ac-125
async def test_undocumented_immersion_heater_value_is_raw_and_flagged(start_emulator, caplog):
    name = "holding_enabled_immersion_heater"
    emu = await start_emulator(PROFILE_615, overrides={("holding_registers", 321): 1})
    with caplog.at_level(logging.WARNING, logger="genesis_wp"):
        client = await update(emu, [name])
    value = client.labels[name]
    assert (value.raw, value.label, value.labeled) == (1, None, False)
    assert client.data[name] == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and r.name.startswith("genesis_wp")]
    assert [r.getMessage() for r in warnings] == [f"{name}: raw value 1 has no documented label"]
