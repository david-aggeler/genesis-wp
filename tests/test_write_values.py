"""A write value is validated before any Modbus request (requirement-33).

Write paths run only against the emulator or the mock unit, never the real device.
"""

from __future__ import annotations

import logging
import asyncio

import pytest

from genesis_wp import (
    GenesisInvalidWriteError, GenesisNotWritableError, GenesisReadOnlyError,
    ThermiaException, ThermiaGenesis,
)
from genesis_wp.const import (
    ATTR_INPUT_OUTDOOR_TEMPERATURE,
    ATTR_COIL_ENABLE_TAP_WATER,
    ATTR_COIL_ENABLE_TWC,
    ATTR_HOLDING_COMFORT_WHEEL_SETTING,
    ATTR_HOLDING_MAXIMUM_PHASE_CURRENT,
    ATTR_HOLDING_TWC_MIX_VALVE_SET_POINT,
    KEY_ADDRESS,
    KEY_DATATYPE,
    KEY_SCALE,
    REGISTERS,
    TYPE_INT,
    TYPE_UINT,
)
from genesis_wp.emulator.profile import HOLDING_REGISTERS

from .harness import PROFILE_615, WPE_I_KIND, emulator_client

pytestmark = pytest.mark.writes

# Holding int at scale 100, holding uint at scale 1, and a coil, all defined for the WPE-I.
INT_SCALE_100 = ATTR_HOLDING_COMFORT_WHEEL_SETTING
UINT_SCALE_1 = ATTR_HOLDING_MAXIMUM_PHASE_CURRENT
COIL = ATTR_COIL_ENABLE_TAP_WATER


def _writer(unit):
    return ThermiaGenesis(unit=unit, kind=WPE_I_KIND, delay=0, read_only=False)


async def _assert_refused(unit, register, value):
    """async_set raises a library exception with no connect, no read and no write."""
    writes = []
    unit.on_write(writes.append)
    with pytest.raises(GenesisInvalidWriteError) as info:
        await _writer(unit).async_set(register, value)
    assert isinstance(info.value, ThermiaException)
    assert not isinstance(info.value, (KeyError, TypeError, ValueError))
    assert not unit.connected
    assert unit.read_events == []
    assert writes == []
    return info.value


def test_register_choice_matches_the_criteria():
    assert (REGISTERS[INT_SCALE_100][KEY_DATATYPE], REGISTERS[INT_SCALE_100][KEY_SCALE]) == (TYPE_INT, 100)
    assert (REGISTERS[UINT_SCALE_1][KEY_DATATYPE], REGISTERS[UINT_SCALE_1][KEY_SCALE]) == (TYPE_UINT, 1)
    for register in (INT_SCALE_100, UINT_SCALE_1, COIL):
        assert REGISTERS[register][WPE_I_KIND]
    for register in (ATTR_COIL_ENABLE_TWC, ATTR_HOLDING_TWC_MIX_VALVE_SET_POINT):
        assert not REGISTERS[register][WPE_I_KIND]


# DHF-TEST: genesis-wp/requirement-33  DHF-TEST: genesis-wp/ac-131
async def test_exact_value_is_written_as_the_rounded_scaled_integer(mock_modbus_unit):
    writes = []
    mock_modbus_unit.on_write(writes.append)
    returned = await _writer(mock_modbus_unit).async_set(INT_SCALE_100, 1.15)
    assert returned == 1.15
    assert [(w.function_code, w.address, list(w.values)) for w in writes] == [
        (6, REGISTERS[INT_SCALE_100][KEY_ADDRESS], [115])
    ]


# DHF-TEST: genesis-wp/requirement-33  DHF-TEST: genesis-wp/ac-131
async def test_emulator_receives_the_rounded_scaled_integer(start_emulator):
    emu = await start_emulator(PROFILE_615)
    client = emulator_client(emu.host, port=emu.port, kind=WPE_I_KIND, delay=0, read_only=False)
    try:
        assert await client.async_set(INT_SCALE_100, 1.15) == 1.15
    finally:
        await client.close()
    assert [(w.block, w.address, tuple(w.values)) for w in emu.writes] == [
        (HOLDING_REGISTERS, REGISTERS[INT_SCALE_100][KEY_ADDRESS], (115,))
    ]


# DHF-TEST: genesis-wp/requirement-33  DHF-TEST: genesis-wp/ac-133
@pytest.mark.parametrize("value", [1.153, 0.001, -1.155])
async def test_value_not_exact_at_the_register_scale_is_refused(mock_modbus_unit, value):
    await _assert_refused(mock_modbus_unit, INT_SCALE_100, value)


# DHF-TEST: genesis-wp/requirement-33  DHF-TEST: genesis-wp/ac-134
@pytest.mark.parametrize(
    ("register", "value"),
    [(INT_SCALE_100, 400), (INT_SCALE_100, -400), (INT_SCALE_100, 327.68), (INT_SCALE_100, -327.69),
     (UINT_SCALE_1, -1), (UINT_SCALE_1, 65536), (INT_SCALE_100, 1e308), (UINT_SCALE_1, 10**400)],
)
async def test_scaled_value_outside_the_16_bit_range_is_refused(mock_modbus_unit, register, value):
    await _assert_refused(mock_modbus_unit, register, value)


# DHF-TEST: genesis-wp/requirement-33  DHF-TEST: genesis-wp/ac-134
@pytest.mark.parametrize(
    ("register", "value", "word"),
    [(INT_SCALE_100, 327.67, 32767), (INT_SCALE_100, -327.68, 32768), (UINT_SCALE_1, 0, 0),
     (UINT_SCALE_1, 65535, 65535)],
)
async def test_scaled_value_at_the_16_bit_bound_is_written(mock_modbus_unit, register, value, word):
    writes = []
    mock_modbus_unit.on_write(writes.append)
    await _writer(mock_modbus_unit).async_set(register, value)
    assert [list(w.values) for w in writes] == [[word]]


# DHF-TEST: genesis-wp/requirement-33  DHF-TEST: genesis-wp/ac-136
@pytest.mark.parametrize("value", ["21", None, float("nan"), float("inf"), float("-inf"), True, [21]])
async def test_non_numeric_or_non_finite_holding_value_is_refused(mock_modbus_unit, value):
    await _assert_refused(mock_modbus_unit, INT_SCALE_100, value)


# DHF-TEST: genesis-wp/requirement-33  DHF-TEST: genesis-wp/ac-137
@pytest.mark.parametrize("value", ["0", "false", 2, -1, 0.5, None])
async def test_coil_value_other_than_0_1_true_false_is_refused(mock_modbus_unit, value):
    await _assert_refused(mock_modbus_unit, COIL, value)


# DHF-TEST: genesis-wp/requirement-33  DHF-TEST: genesis-wp/ac-137
@pytest.mark.parametrize(("value", "sent"), [(0, False), (1, True), (False, False), (True, True)])
async def test_coil_value_0_1_true_false_is_written(mock_modbus_unit, value, sent):
    writes = []
    mock_modbus_unit.on_write(writes.append)
    await _writer(mock_modbus_unit).async_set(COIL, value)
    assert [(w.function_code, list(w.values)) for w in writes] == [(5, [sent])]


# DHF-TEST: genesis-wp/requirement-33  DHF-TEST: genesis-wp/ac-138
@pytest.mark.parametrize("register", ["holding_typo", "", None])
async def test_unknown_register_name_raises_a_library_exception(mock_modbus_unit, register):
    await _assert_refused(mock_modbus_unit, register, 1)


# DHF-TEST: genesis-wp/requirement-33  DHF-TEST: genesis-wp/ac-139
@pytest.mark.parametrize(("register", "value"), [(ATTR_COIL_ENABLE_TWC, 1), (ATTR_HOLDING_TWC_MIX_VALVE_SET_POINT, 21)])
async def test_register_not_defined_for_the_model_is_refused(mock_modbus_unit, register, value):
    await _assert_refused(mock_modbus_unit, register, value)


# DHF-TEST: genesis-wp/requirement-38  DHF-TEST: genesis-wp/ac-151
@pytest.mark.parametrize(
    ("read_only", "register", "value", "error"),
    [(True, INT_SCALE_100, 1, GenesisReadOnlyError),
     (False, ATTR_INPUT_OUTDOOR_TEMPERATURE, 1, GenesisNotWritableError),
     (False, INT_SCALE_100, 1.153, GenesisInvalidWriteError),
     (False, COIL, 2, GenesisInvalidWriteError),
     (False, INT_SCALE_100, None, GenesisInvalidWriteError),
     (False, INT_SCALE_100, 1e308, GenesisInvalidWriteError),
     (False, INT_SCALE_100, 400, GenesisInvalidWriteError),
     (False, "holding_typo", 1, GenesisInvalidWriteError),
     (False, ATTR_COIL_ENABLE_TWC, 1, GenesisInvalidWriteError)],
)
async def test_refused_write_logs_one_warning(mock_modbus_unit, caplog, read_only, register, value, error):
    writes = []
    mock_modbus_unit.on_write(writes.append)
    client = ThermiaGenesis(unit=mock_modbus_unit, kind=WPE_I_KIND, delay=0, read_only=read_only)
    with caplog.at_level(logging.WARNING, logger="genesis_wp"):
        with pytest.raises(error) as info:
            await client.async_set(register, value)
    records = [record for record in caplog.records if record.name == "genesis_wp"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert register in records[0].getMessage()
    assert records[0].getMessage() == str(info.value)
    assert not mock_modbus_unit.connected
    assert mock_modbus_unit.read_events == []
    assert writes == []


# DHF-TEST: genesis-wp/requirement-33  DHF-TEST: genesis-wp/ac-152
async def test_value_inside_scale_tolerance_writes_115(mock_modbus_unit):
    writes = []
    mock_modbus_unit.on_write(writes.append)
    await _writer(mock_modbus_unit).async_set(INT_SCALE_100, 1.15 + 1e-9)
    assert [(w.function_code, w.address, list(w.values)) for w in writes] == [
        (6, REGISTERS[INT_SCALE_100][KEY_ADDRESS], [115])
    ]


# DHF-TEST: genesis-wp/requirement-33  DHF-TEST: genesis-wp/ac-152
async def test_value_outside_scale_tolerance_is_refused(mock_modbus_unit):
    await _assert_refused(mock_modbus_unit, INT_SCALE_100, 1.15 + 1e-5)


# DHF-TEST: genesis-wp/requirement-33  DHF-TEST: genesis-wp/ac-169
@pytest.mark.parametrize(("register", "value"), [
    ("holding_operational_mode", 0), ("holding_enabled_immersion_heater", 1),
])
async def test_undeclared_enum_write_is_refused(mock_modbus_unit, caplog, register, value):
    with caplog.at_level(logging.WARNING, logger="genesis_wp"):
        error = await _assert_refused(mock_modbus_unit, register, value)
    assert [r.getMessage() for r in caplog.records if r.name == "genesis_wp"] == [str(error)]


# DHF-TEST: genesis-wp/requirement-33  DHF-TEST: genesis-wp/ac-169
async def test_declared_enum_write_is_accepted(mock_modbus_unit):
    writes = []
    mock_modbus_unit.on_write(writes.append)
    await _writer(mock_modbus_unit).async_set("holding_operational_mode", 2)
    assert [(w.function_code, list(w.values)) for w in writes] == [(6, [2])]


# DHF-TEST: genesis-wp/requirement-33  DHF-TEST: genesis-wp/ac-170
@pytest.mark.parametrize("selector", ["unread", 0, 1, None, 2])
async def test_valid_when_write_uses_last_read_selector(mock_modbus_unit, caplog, selector):
    source = "holding_outdoor_temperature_source"
    sensor = "holding_outdoor_temperature_sensor"
    client = _writer(mock_modbus_unit)
    if selector != "unread":
        mock_modbus_unit.load_raw({"holding": {117: 0 if selector is None else selector}})
        if selector is None:
            from modbus_connection import ModbusExceptionError
            mock_modbus_unit.fail_read(117, ModbusExceptionError(2), register_type="holding")
        await client.async_update(only_registers=[source])
        await client.close()
        await mock_modbus_unit.disconnect()
    writes = []
    mock_modbus_unit.on_write(writes.append)
    reads = list(mock_modbus_unit.read_events)
    if selector == 1:
        await client.async_set(sensor, 7.35)
        assert [(w.function_code, w.address, list(w.values)) for w in writes] == [(6, 118, [735])]
    else:
        with caplog.at_level(logging.WARNING, logger="genesis_wp"):
            with pytest.raises(GenesisInvalidWriteError) as error:
                await client.async_set(sensor, 7.35)
        assert not mock_modbus_unit.connected
        assert writes == []
        assert [r.getMessage() for r in caplog.records if r.name == "genesis_wp"][-1:] == [str(error.value)]
    assert mock_modbus_unit.read_events == reads


# DHF-TEST: genesis-wp/requirement-33  DHF-TEST: genesis-wp/ac-170
@pytest.mark.parametrize("boundary", ["connection", "delay"])
@pytest.mark.parametrize("selector", [0, None, 1])
async def test_conditional_write_rechecks_after_suspension(mock_modbus_unit, monkeypatch, boundary, selector):
    source = "holding_outdoor_temperature_source"
    client = _writer(mock_modbus_unit)
    mock_modbus_unit.load_raw({"holding": {117: 1}})
    await client.async_update(only_registers=[source])
    writes = []
    mock_modbus_unit.on_write(writes.append)
    paused, resume = asyncio.Event(), asyncio.Event()
    original = client._ensure_unit if boundary == "connection" else asyncio.sleep

    async def suspend_write(*args):
        if asyncio.current_task() is write_task:
            paused.set()
            await resume.wait()
        return await original(*args)

    if boundary == "connection":
        monkeypatch.setattr(client, "_ensure_unit", suspend_write)
    else:
        monkeypatch.setattr("genesis_wp.asyncio.sleep", suspend_write)
    write_task = asyncio.create_task(client.async_set("holding_outdoor_temperature_sensor", 7.35))
    try:
        await asyncio.wait_for(paused.wait(), timeout=2)
        mock_modbus_unit.load_raw({"holding": {117: selector or 0}})
        if selector is None:
            from modbus_connection import ModbusExceptionError
            mock_modbus_unit.fail_read(117, ModbusExceptionError(2), register_type="holding")
        await client.async_update(only_registers=[source])
    finally:
        resume.set()
    if selector == 1:
        assert await write_task == 7.35
        assert [(w.function_code, w.address, list(w.values)) for w in writes] == [(6, 118, [735])]
    else:
        with pytest.raises(GenesisInvalidWriteError):
            await write_task
        assert writes == []
