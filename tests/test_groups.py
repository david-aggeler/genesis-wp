"""Every enabled register has one group and one category (requirement-12, design_decision-1)."""

from __future__ import annotations

import pytest

from genesis_wp import register_category, register_concerns, register_group
from genesis_wp.const import MODEL_NAMES, MODEL_STIEBEL_WPE_I_071, REGISTERS
from genesis_wp.groups import CATEGORIES, GROUP_CATEGORY, GROUPS, REGISTER_GROUPS


# DHF-REQ: genesis-wp/requirement-12  DHF-TEST: genesis-wp/ac-35
@pytest.mark.parametrize("kind", list(MODEL_NAMES))
def test_every_enabled_register_has_one_group_and_category(kind):
    enabled = [name for name, meta in REGISTERS.items() if meta[kind]]
    for name in enabled:
        assert register_group(name) in GROUPS, name
        assert register_category(name) in CATEGORIES, name
    if kind == MODEL_STIEBEL_WPE_I_071:
        assert len(enabled) == 268


def test_group_table_names_only_known_registers_and_groups():
    assert set(REGISTER_GROUPS) <= set(REGISTERS)
    assert set(REGISTER_GROUPS.values()) <= set(GROUP_CATEGORY)


# DHF-REQ: genesis-wp/requirement-12  DHF-TEST: genesis-wp/ac-36
def test_sg_ready_registers_share_the_power_consumption_control_group():
    for name in ("dinput_smart_grid_1", "dinput_smart_grid_2", "holding_power_consumption_control",
                 "holding_input_power_limit", "input_comfort_mode"):
        assert REGISTERS[name][MODEL_STIEBEL_WPE_I_071], name
        assert register_group(name) == "power_consumption_control", name
    for name in ("input_current_power_output", "input_current_consumed_power", "holding_maximum_phase_current"):
        assert register_group(name) == "energy", name


# DHF-REQ: genesis-wp/requirement-12  DHF-TEST: genesis-wp/ac-37
def test_sensor_alarm_names_the_group_it_concerns():
    name = "dinput_mix_valve_1_supply_line_sensor_alarm"
    assert register_group(name) == "alarms"
    assert register_concerns(name) == "heating_circuit_2"


def test_concerns_is_none_outside_alarms_and_for_controller_wide_alarms():
    assert register_concerns("input_buffer_tank_temperature") is None
    assert register_concerns("dinput_alarm_active_class_a") is None
    for name, group in REGISTER_GROUPS.items():
        concerned = register_concerns(name)
        assert concerned is None or concerned in GROUP_CATEGORY, name
