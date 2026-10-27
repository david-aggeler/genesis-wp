"""Plant capabilities from read-only evidence (requirement-13, design_decision-2)."""

from __future__ import annotations

import pytest

from genesis_wp import ABSENT, PRESENT, UNKNOWN, ThermiaGenesis
from genesis_wp.capabilities import STATES
from genesis_wp.const import MODEL_STIEBEL_WPE_I_071, REGISTERS

from .harness import PROFILE_615, PROFILES


async def updated_client(emu, **options) -> ThermiaGenesis:
    client = ThermiaGenesis(emu.host, port=emu.port, kind=emu.profile.model, delay=0, **options)
    try:
        await client.async_update()
    finally:
        await client.close()
    return client


# DHF-REQ: genesis-wp/requirement-13  DHF-TEST: genesis-wp/ac-38
async def test_hot_water_is_absent_on_the_captured_plant(start_emulator):
    emu = await start_emulator(PROFILE_615)
    hot_water = (await updated_client(emu)).capabilities["hot_water"]
    assert hot_water.state == ABSENT
    evidence = " | ".join(hot_water.evidence)
    assert "coil 8 coil_enable_tap_water = 0" in evidence
    for address in (15, 16, 17):
        assert f"input {address} " in evidence
    assert evidence.count("not fitted") == 3


# DHF-REQ: genesis-wp/requirement-13  DHF-TEST: genesis-wp/ac-39
async def test_electric_meter_absent_when_all_phase_voltages_read_0(start_emulator):
    emu = await start_emulator(PROFILE_615)
    assert all(emu.profile.blocks["input_registers"].value(a) == 0 for a in range(72, 78))
    assert (await updated_client(emu)).capabilities["electric_meter"].state == ABSENT


# DHF-REQ: genesis-wp/requirement-13  DHF-TEST: genesis-wp/ac-40
async def test_electric_meter_present_when_any_phase_voltage_is_above_0(start_emulator):
    emu = await start_emulator(PROFILE_615, overrides={("input_registers", 72): 23000})  # 230 V at scale 100
    client = await updated_client(emu)
    assert client.data["input_electric_meter_l1_0_voltage"] == 230
    assert client.data["input_electric_meter_l1_current"] == 0
    assert client.data["input_electric_meter_l1_power"] == 0
    assert client.capabilities["electric_meter"].state == PRESENT


# DHF-REQ: genesis-wp/requirement-13  DHF-TEST: genesis-wp/ac-41
async def test_smart_grid_unknown_until_its_input_is_seen_non_zero(start_emulator):
    first = await start_emulator(PROFILE_615)
    client = ThermiaGenesis(first.host, port=first.port, kind=first.profile.model, delay=0)
    await client.async_update()
    await client.close()
    assert client.data["dinput_smart_grid_2"] in (0, False)
    assert client.data["holding_power_consumption_control"] == 0
    assert client.capabilities["smart_grid"].state == UNKNOWN

    second = await start_emulator(PROFILE_615, overrides={("discrete_inputs", 204): 1})
    client._host, client._port = second.host, second.port  # same client, the plant now shows DI22 = 1
    await client.async_update()
    await client.close()
    assert client.capabilities["smart_grid"].state == PRESENT

    client._host, client._port = first.host, first.port  # DI22 back to 0: stays present
    await client.async_update()
    await client.close()
    assert client.capabilities["smart_grid"].state == PRESENT
    assert "seen non-zero in an earlier update" in client.capabilities["smart_grid"].evidence


# DHF-REQ: genesis-wp/requirement-13  DHF-TEST: genesis-wp/ac-41
async def test_smart_grid_present_when_sg_mode_is_non_zero(start_emulator):
    emu = await start_emulator(PROFILE_615, overrides={("holding_registers", 124): 1})
    assert (await updated_client(emu)).capabilities["smart_grid"].state == PRESENT


# DHF-REQ: genesis-wp/requirement-13  DHF-TEST: genesis-wp/ac-42
async def test_mixing_circuit_present_when_coil_11_is_on(start_emulator):
    emu = await start_emulator(PROFILE_615)
    assert emu.profile.blocks["coils"].value(11) == 1
    assert (await updated_client(emu)).capabilities["heating_circuit_2"].state == PRESENT


async def test_coil_off_with_a_fitted_sensor_is_installed_but_switched_off(start_emulator):
    emu = await start_emulator(PROFILE_615, overrides={("coils", 11): 0})
    hc2 = (await updated_client(emu)).capabilities["heating_circuit_2"]
    assert (hc2.state, hc2.enabled) == (PRESENT, False)  # T32 still reads a temperature


# DHF-REQ: genesis-wp/requirement-13  DHF-TEST: genesis-wp/ac-43
async def test_capability_detection_sends_no_write(start_emulator):
    emu = await start_emulator(PROFILE_615)
    client = await updated_client(emu)
    assert client.read_only
    client.override_capability("hot_water", PRESENT)
    assert client.capabilities
    assert emu.writes == []
    assert {r.function_code for r in emu.requests} <= {1, 2, 3, 4}


# DHF-REQ: genesis-wp/requirement-13  DHF-TEST: genesis-wp/ac-44
@pytest.mark.parametrize("profile", PROFILES)
async def test_each_capability_reports_evidence_and_gated_registers(start_emulator, profile):
    emu = await start_emulator(profile)
    client = await updated_client(emu)
    assert client.capabilities
    for name, capability in client.capabilities.items():
        assert capability.state in STATES, name
        assert capability.evidence, name
        assert capability.gates, name
        assert all(REGISTERS[reg][MODEL_STIEBEL_WPE_I_071] for reg in capability.gates), name


async def test_captured_plant_capabilities(start_emulator):
    """The owner's plant as captured (formal_review-1)."""
    emu = await start_emulator(PROFILE_615)
    states = {name: cap.state for name, cap in (await updated_client(emu)).capabilities.items()}
    assert {n for n, s in states.items() if s == PRESENT} == {"heating_circuit_2", "buffer_tank", "ksm"}
    # Switched off without a sensor that proves absence: unknown, not absent (design_decision-2, 2026-10-27)
    assert {n for n, s in states.items() if s == UNKNOWN} == {
        "smart_grid", "additional_heater", "passive_cooling", "hot_gas_recovery", "cooling",
    }
    for name in ("hot_water", "pool", "active_cooling", "room_sensor", "electric_meter"):
        assert states[name] == ABSENT, name


async def test_model_without_registers_reports_absent(start_emulator):
    emu = await start_emulator(PROFILE_615)
    client = await updated_client(emu)
    assert "heating_circuit_3" not in client.capabilities
    assert client.capability_state("heating_circuit_3") == ABSENT
    assert client.capability_state("hot_water") == ABSENT


async def test_consumer_override(start_emulator):
    emu = await start_emulator(PROFILE_615)
    client = await updated_client(emu, capability_overrides={"electric_meter": PRESENT})
    meter = client.capabilities["electric_meter"]
    assert meter.state == PRESENT and meter.overridden
    assert "detected absent" in meter.evidence[0]
    client.override_capability("electric_meter", None)
    assert client.capabilities["electric_meter"].state == ABSENT


def test_override_rejects_unknown_names_and_states():
    with pytest.raises(ValueError, match="unknown capability"):
        ThermiaGenesis("127.0.0.1", kind=MODEL_STIEBEL_WPE_I_071, capability_overrides={"sauna": PRESENT})
    with pytest.raises(ValueError, match="state must be"):
        ThermiaGenesis("127.0.0.1", kind=MODEL_STIEBEL_WPE_I_071, capability_overrides={"pool": "maybe"})


def test_capability_state_before_any_update_is_unknown():
    client = ThermiaGenesis("127.0.0.1", kind=MODEL_STIEBEL_WPE_I_071)
    assert client.capability_state("hot_water") == UNKNOWN
    with pytest.raises(ValueError):
        client.capability_state("sauna")


# DHF-REQ: genesis-wp/requirement-18  DHF-TEST: genesis-wp/ac-59
# DHF-REQ: genesis-wp/requirement-18  DHF-TEST: genesis-wp/ac-59
async def test_cooling_on_the_captured_plant_gates_the_cooling_category(start_emulator):
    from genesis_wp import register_category

    emu = await start_emulator(PROFILE_615)
    cooling = (await updated_client(emu)).capabilities["cooling"]
    assert cooling.state == UNKNOWN  # passive cooling switched off, not proven missing
    for name in ("coil_enable_outdoor_temp_dependent_for_cooling_with_mixing_valve_1",
                 "coil_enable_internal_brine_pump_to_start_when_cooling_is_active_for_mixing_valve_1",
                 "holding_set_point_for_supply_line_temp_passive_cooling_with_mixing_valve_1",
                 "holding_set_point_minimum_outdoor_temp_when_cooling_is_permitted",
                 "input_cooling_season_integral_value"):
        assert name in cooling.gates, name
    assert all(register_category(name) == "cooling" for name in cooling.gates)
    assert any("active_cooling = absent" in e for e in cooling.evidence)
    overridden = await updated_client(emu, capability_overrides={"passive_cooling": ABSENT})
    assert overridden.capabilities["cooling"].state == ABSENT


# DHF-REQ: genesis-wp/requirement-13  DHF-TEST: genesis-wp/ac-64
async def test_switched_off_module_is_switched_off_not_missing(start_emulator):
    caps = (await updated_client(await start_emulator(PROFILE_615))).capabilities
    assert (caps["passive_cooling"].state, caps["passive_cooling"].enabled) == (UNKNOWN, False)
    assert (caps["hot_water"].state, caps["hot_water"].enabled) == (ABSENT, False)
    assert (caps["heating_circuit_2"].state, caps["heating_circuit_2"].enabled) == (PRESENT, True)
    assert caps["buffer_tank"].enabled is None  # no enable coil


# DHF-REQ: genesis-wp/requirement-13  DHF-TEST: genesis-wp/ac-65
async def test_passive_cooling_seen_running_stays_installed(start_emulator):
    running = await start_emulator(PROFILE_615, overrides={("coils", 33): 1, ("discrete_inputs", 245): 1})
    client = ThermiaGenesis(running.host, port=running.port, kind=running.profile.model, delay=0)
    await client.async_update()
    await client.close()
    assert client.capabilities["passive_cooling"].state == PRESENT
    off = await start_emulator(PROFILE_615)  # coil 33 = 0, DI 245 = 0
    client._host, client._port = off.host, off.port
    await client.async_update()
    await client.close()
    passive = client.capabilities["passive_cooling"]
    assert (passive.state, passive.enabled) == (PRESENT, False)
    assert "seen running in an earlier update" in passive.evidence


async def test_cooling_unknown_when_a_member_is_unknown(start_emulator):
    # coil 33 off while a passive-cooling indication is fitted is not modeled; override a member instead
    emu = await start_emulator(PROFILE_615)
    client = await updated_client(emu, capability_overrides={"passive_cooling": UNKNOWN})
    assert client.capabilities["cooling"].state == UNKNOWN
