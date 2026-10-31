"""A write either takes effect or raises a library exception (requirement-30).

Write paths run only against the emulator or the mock unit, never the real device.
"""

from __future__ import annotations

import pytest
from modbus_connection import ModbusConnectionError, ModbusTimeoutError

from genesis_wp import ThermiaConnectionError, ThermiaException, ThermiaGenesis
from genesis_wp.const import (
    ATTR_COIL_ENABLE_TAP_WATER,
    ATTR_DINPUT_ALARM_ACTIVE_CLASS_A,
    ATTR_HOLDING_COMFORT_WHEEL_SETTING,
    ATTR_INPUT_OUTDOOR_TEMPERATURE,
    KEY_ADDRESS,
    REGISTERS,
)
from genesis_wp.emulator.profile import COILS, HOLDING_REGISTERS

from .harness import PROFILE_615, WPE_I_KIND
from .test_logging_hygiene import assert_no_host_in_chain

pytestmark = pytest.mark.writes

NOT_WRITABLE = [ATTR_INPUT_OUTDOOR_TEMPERATURE, ATTR_DINPUT_ALARM_ACTIVE_CLASS_A]


def _address(register):
    return REGISTERS[register][KEY_ADDRESS]


# DHF-TEST: genesis-wp/requirement-30  DHF-TEST: genesis-wp/ac-114
@pytest.mark.parametrize("register", NOT_WRITABLE)
async def test_write_to_a_read_only_register_type_raises_before_any_request(start_emulator, register):
    emu = await start_emulator(PROFILE_615)
    client = ThermiaGenesis(emu.host, port=emu.port, kind=WPE_I_KIND, delay=0, read_only=False)
    try:
        await client.async_update(only_registers=[ATTR_HOLDING_COMFORT_WHEEL_SETTING])
        before = len(emu.requests)
        with pytest.raises(ThermiaException) as info:
            await client.async_set(register, 5)
    finally:
        await client.close()
    assert not isinstance(info.value, TypeError)
    assert len(emu.requests) == before
    assert emu.writes == []


# DHF-TEST: genesis-wp/requirement-30  DHF-TEST: genesis-wp/ac-114
@pytest.mark.parametrize("register", NOT_WRITABLE)
async def test_mock_unit_sees_no_request_for_a_read_only_register_type(mock_modbus_unit, register):
    writes = []
    mock_modbus_unit.on_write(writes.append)
    client = ThermiaGenesis(unit=mock_modbus_unit, kind=WPE_I_KIND, delay=0, read_only=False)
    with pytest.raises(ThermiaException) as info:
        await client.async_set(register, 5)
    assert not isinstance(info.value, TypeError)
    assert writes == []
    assert mock_modbus_unit.read_events == []


# DHF-TEST: genesis-wp/requirement-30  DHF-TEST: genesis-wp/ac-115
@pytest.mark.parametrize(
    ("block", "register", "value"),
    [(HOLDING_REGISTERS, ATTR_HOLDING_COMFORT_WHEEL_SETTING, 21), (COILS, ATTR_COIL_ENABLE_TAP_WATER, 1)],
)
async def test_rejected_write_raises_a_host_free_connection_error(start_emulator, block, register, value):
    emu = await start_emulator(PROFILE_615)
    emu.inject_fault(block, _address(register))
    client = ThermiaGenesis(emu.host, port=emu.port, kind=WPE_I_KIND, delay=0, read_only=False)
    try:
        with pytest.raises(ThermiaConnectionError) as info:
            await client.async_set(register, value)
    finally:
        await client.close()
    assert [(w.block, w.address) for w in emu.writes] == [(block, _address(register))]
    assert emu.writes[0].rejected is not None
    assert_no_host_in_chain(info.value)


# DHF-TEST: genesis-wp/requirement-30  DHF-TEST: genesis-wp/ac-116
@pytest.mark.parametrize("error_class", [ModbusConnectionError, ModbusTimeoutError, OSError])
async def test_transport_failure_on_write_raises_a_host_free_connection_error(mock_modbus_unit, error_class):
    # The transport's own text names the host; none of it may reach the caller.
    mock_modbus_unit.fail_write(_address(ATTR_HOLDING_COMFORT_WHEEL_SETTING), error_class("lost link to 127.0.0.1:502"))
    client = ThermiaGenesis(unit=mock_modbus_unit, kind=WPE_I_KIND, delay=0, read_only=False)
    with pytest.raises(ThermiaConnectionError) as info:
        await client.async_set(ATTR_HOLDING_COMFORT_WHEEL_SETTING, 21)
    assert "unit" in info.value.message
    assert_no_host_in_chain(info.value)
