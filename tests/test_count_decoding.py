"""Count-kind registers decode to int when their scaled value is integral (requirement-35)."""

from __future__ import annotations

import logging

import pytest

from genesis_wp import ThermiaGenesis
from genesis_wp.const import KEY_KIND, KEY_NOT_FITTED, KIND_COUNT, MODEL_STIEBEL_WPE_I_071, REGISTERS

from .harness import PROFILE_615, PROFILES, expected_library_values, library_full_update

pytestmark = pytest.mark.fixture

AVAILABLE_GEARS = "input_compressor_available_gears"  # input 4, count, scale 100
CURRENT_GEAR = "input_compressor_current_gear"  # input 61, count, scale 100
CURRENT_GEAR_BY_PROFILE = {"stiebel-wpe-i-07.1-plus-6.0.6": 1, "stiebel-wpe-i-07.1-plus-6.1.5": 3}


def is_count(name: str) -> bool:
    return REGISTERS[name].get(KEY_KIND, {}).get(MODEL_STIEBEL_WPE_I_071) == KIND_COUNT


# DHF-REQ: genesis-wp/requirement-35  DHF-TEST: genesis-wp/ac-142
@pytest.mark.parametrize("profile", PROFILES)
async def test_integral_count_registers_decode_to_int(start_emulator, profile):
    emu = await start_emulator(profile)
    got = await library_full_update(emu)
    expected = expected_library_values(profile)
    integral = {n: v for n, v in got.items() if is_count(n) and v is not None and float(v).is_integer()}
    assert AVAILABLE_GEARS in integral and CURRENT_GEAR in integral
    for name, value in integral.items():
        assert type(value) is int, f"{name}: {value!r} is {type(value).__name__}"
        assert value == expected[name], name
    assert got[AVAILABLE_GEARS] == 12
    assert got[CURRENT_GEAR] == CURRENT_GEAR_BY_PROFILE[profile]


# DHF-REQ: genesis-wp/requirement-35  DHF-TEST: genesis-wp/ac-143
async def test_non_integral_count_stays_float_with_a_debug_record(start_emulator, caplog):
    emu = await start_emulator(PROFILE_615, overrides={("input_registers", 4): 1250})
    with caplog.at_level(logging.DEBUG, logger="genesis_wp"):
        got = await library_full_update(emu)
    assert type(got[AVAILABLE_GEARS]) is float
    assert got[AVAILABLE_GEARS] == 12.5
    records = [
        r for r in caplog.records
        if r.name == "genesis_wp" and r.levelno == logging.DEBUG and AVAILABLE_GEARS in r.getMessage()
    ]
    assert len(records) == 1, [r.getMessage() for r in records]


# DHF-REQ: genesis-wp/requirement-35  DHF-TEST: genesis-wp/ac-144
@pytest.mark.parametrize("profile", PROFILES)
async def test_non_count_registers_and_sentinels_decode_as_before(start_emulator, profile):
    emu = await start_emulator(profile)
    got = await library_full_update(emu)
    expected = expected_library_values(profile)
    assert set(got) == set(expected)
    for name, value in got.items():
        if is_count(name):
            if expected[name] is None:
                assert value is None, name
            continue
        assert value == expected[name] or (value is None and expected[name] is None), name
        assert type(value) is type(expected[name]), f"{name}: {type(value).__name__} != {type(expected[name]).__name__}"


# DHF-TEST: genesis-wp/requirement-35
async def test_not_fitted_sentinel_on_a_count_register_still_decodes_to_none(mock_modbus_unit, monkeypatch):
    """No count register declares a sentinel yet; one that does must still decode to None."""
    monkeypatch.setitem(REGISTERS, AVAILABLE_GEARS, {**REGISTERS[AVAILABLE_GEARS], KEY_NOT_FITTED: {MODEL_STIEBEL_WPE_I_071: 20000}})
    mock_modbus_unit.load_raw({"input": {4: 20000}})
    client = ThermiaGenesis(unit=mock_modbus_unit, kind=MODEL_STIEBEL_WPE_I_071, delay=0)
    data = await client.async_update(only_registers=[AVAILABLE_GEARS])
    assert data[AVAILABLE_GEARS] is None


async def test_models_without_a_declared_kind_keep_the_float(mock_modbus_unit):
    """KEY_KIND is declared for the WPE-I only: another model still reads a scaled float."""
    model = next(m for m in ("mega", "inverter", "calibra_rxt") if REGISTERS[AVAILABLE_GEARS].get(m))
    mock_modbus_unit.load_raw({"input": {4: 1200}})
    client = ThermiaGenesis(unit=mock_modbus_unit, kind=model, delay=0)
    data = await client.async_update(only_registers=[AVAILABLE_GEARS])
    assert type(data[AVAILABLE_GEARS]) is float and data[AVAILABLE_GEARS] == 12.0
