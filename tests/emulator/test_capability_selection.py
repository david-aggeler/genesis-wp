"""Emulator capability selection, checked with the library's detection (requirement-14)."""

from __future__ import annotations

import hashlib
from importlib import resources

import pytest

from genesis_wp import ABSENT, PRESENT, UNKNOWN, ThermiaGenesis
from genesis_wp.emulator import Emulator, get_profile
from genesis_wp.emulator.capabilities import CapabilityError, parse_selection
from genesis_wp.emulator.cli import main

from ..harness import PROFILE_615


async def detected(emu):
    client = ThermiaGenesis(emu.host, port=emu.port, kind=emu.profile.model, delay=0)
    try:
        await client.async_update()
    finally:
        await client.close()
    return client


def profile_digest(name: str) -> str:
    data = (resources.files("genesis_wp.emulator") / "profiles" / f"{name}.json").read_bytes()
    return hashlib.sha256(data).hexdigest()


# DHF-REQ: genesis-wp/requirement-14  DHF-TEST: genesis-wp/ac-45
async def test_hot_water_on_is_detected_with_fitted_sensors(start_emulator):
    emu = await start_emulator(PROFILE_615, capabilities={"hot_water": True})
    client = await detected(emu)
    assert client.capabilities["hot_water"].state == PRESENT
    for name in ("input_tap_water_top_temperature", "input_tap_water_lower_temperature", "input_tap_water_weighted_temperature"):
        assert isinstance(client.data[name], float), name
    notes = [n for n in emu.profile.source["notes"] if n.startswith("capability selection hot_water=on")]
    assert notes and "synthetic" in notes[0] and "input_registers 15" in notes[0]


# DHF-REQ: genesis-wp/requirement-14  DHF-TEST: genesis-wp/ac-46
async def test_mixing_circuit_off_is_detected_absent(start_emulator):
    emu = await start_emulator(PROFILE_615, capabilities={"heating_circuit_2": False})
    client = await detected(emu)
    assert client.capabilities["heating_circuit_2"].state == ABSENT
    assert client.data["input_mix_valve_1_supply_line_temperature"] is None


# DHF-REQ: genesis-wp/requirement-14  DHF-TEST: genesis-wp/ac-47
async def test_without_selection_the_captured_plant_is_presented(start_emulator):
    before = profile_digest(PROFILE_615)
    emu = await start_emulator(PROFILE_615)
    states = {n: c.state for n, c in (await detected(emu)).capabilities.items()}
    for name in ("heating_circuit_2", "buffer_tank", "ksm"):
        assert states[name] == PRESENT, name
    for name in ("hot_water", "pool", "active_cooling", "room_sensor", "electric_meter"):
        assert states[name] == ABSENT, name
    assert states["smart_grid"] == UNKNOWN
    assert profile_digest(PROFILE_615) == before


async def test_selection_never_touches_the_packaged_profile(start_emulator):
    before = profile_digest(PROFILE_615)
    await start_emulator(PROFILE_615, capabilities={"hot_water": True, "pool": True, "electric_meter": True})
    assert profile_digest(PROFILE_615) == before
    assert not any(n.startswith("capability selection") for n in get_profile(PROFILE_615).source["notes"])


@pytest.mark.parametrize("name, on", [
    ("hot_water", True), ("hot_water", False), ("heating_circuit_2", True), ("heating_circuit_2", False),
    ("buffer_tank", True), ("buffer_tank", False), ("pool", True), ("pool", False),
    ("active_cooling", True), ("active_cooling", False), ("passive_cooling", True),
    ("hot_gas_recovery", True), ("room_sensor", True), ("room_sensor", False),
    ("electric_meter", True), ("electric_meter", False), ("smart_grid", True), ("additional_heater", True),
    ("ksm", True),
])
async def test_every_selectable_capability_is_detected_as_selected(start_emulator, name, on):
    emu = await start_emulator(PROFILE_615, capabilities={name: on})
    assert (await detected(emu)).capabilities[name].state == (PRESENT if on else ABSENT)


@pytest.mark.parametrize("selection, message", [
    ({"sauna": True}, "unknown capability"),
    ({"heating_circuit_3": True}, "has no registers"),
    ({"ksm": False}, "built into"),
    ({"smart_grid": False}, "cannot be switched off"),
    ({"additional_heater": False}, "cannot be switched off"),
])
def test_selections_the_plant_cannot_present_are_rejected(selection, message):
    with pytest.raises(CapabilityError, match=message):
        Emulator(get_profile(PROFILE_615), port=0, capabilities=selection)


def test_parse_selection():
    assert parse_selection(["pool=on", "hot_water=off"]) == {"pool": True, "hot_water": False}
    with pytest.raises(CapabilityError):
        parse_selection(["pool"])


def test_cli_exits_non_zero_on_a_bad_capability(capsys):
    assert main(["--capability", "heating_circuit_4=on"]) == 2
    assert "has no registers" in capsys.readouterr().err


# DHF-REQ: genesis-wp/requirement-18  DHF-TEST: genesis-wp/ac-60
async def test_passive_cooling_on_makes_cooling_present(start_emulator):
    emu = await start_emulator(PROFILE_615, capabilities={"passive_cooling": True})
    caps = (await detected(emu)).capabilities
    assert caps["passive_cooling"].state == PRESENT
    assert caps["active_cooling"].state == ABSENT
    assert caps["cooling"].state == PRESENT


@pytest.mark.parametrize("name", ["passive_cooling", "hot_gas_recovery", "cooling"])
def test_off_is_rejected_where_detection_cannot_show_absence(name):
    with pytest.raises(CapabilityError, match="cannot be switched off"):
        Emulator(get_profile(PROFILE_615), port=0, capabilities={name: False})


def test_cooling_on_is_ambiguous():
    with pytest.raises(CapabilityError, match="active_cooling or passive_cooling"):
        Emulator(get_profile(PROFILE_615), port=0, capabilities={"cooling": True})
