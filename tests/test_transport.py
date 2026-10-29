"""Transport on Home Assistant's modbus-connection (requirement-2)."""

from __future__ import annotations

import asyncio
import logging

import pytest
from modbus_connection import ModbusTcpParams
from modbus_connection.pymodbus import ModbusConnection

from genesis_wp import ThermiaConnectionError, ThermiaGenesis

from .harness import PROFILE_615, UNIT, WPE_I_KIND, expected_library_values, library_full_update

pytestmark = pytest.mark.fixture

FIRMWARE = ["input_control_software_version_major", "input_control_software_version_minor", "input_control_software_version_micro"]


# DHF-REQ: genesis-wp/requirement-2  DHF-TEST: genesis-wp/ac-4
async def test_full_update_does_not_block_the_event_loop(start_emulator, caplog):
    emu = await start_emulator(PROFILE_615)
    loop = asyncio.get_running_loop()
    debug, threshold = loop.get_debug(), loop.slow_callback_duration
    loop.set_debug(True)
    loop.slow_callback_duration = 0.1
    try:
        with caplog.at_level(logging.WARNING, logger="asyncio"):
            # delay=0 so the update is pure I/O; the inherited 0.1 s delay is an await, not a block.
            client = ThermiaGenesis(emu.host, port=emu.port, kind=WPE_I_KIND, delay=0)
            await client.async_update()
            await client.close()
    finally:
        loop.set_debug(debug)
        loop.slow_callback_duration = threshold
    slow = [r.getMessage() for r in caplog.records if "Executing" in r.getMessage() and "took" in r.getMessage()]
    assert slow == []


# DHF-REQ: genesis-wp/requirement-2  DHF-TEST: genesis-wp/ac-6
async def test_upper_case_hostname_is_accepted(start_emulator):
    emu = await start_emulator(PROFILE_615)
    client = ThermiaGenesis("LOCALHOST", port=emu.port, kind=WPE_I_KIND, delay=0)
    data = await client.async_update(only_registers=FIRMWARE)
    await client.close()
    assert [data[name] for name in FIRMWARE] == [6, 1, 5]


# DHF-REQ: genesis-wp/requirement-2  DHF-TEST: genesis-wp/ac-24
async def test_client_runs_on_an_injected_unit(start_emulator):
    emu = await start_emulator(PROFILE_615)
    connection = ModbusConnection(ModbusTcpParams(host=emu.host, port=emu.port))
    await connection.connect()
    try:
        client = ThermiaGenesis(unit=connection.for_unit(UNIT), kind=WPE_I_KIND, delay=0, max_registers=16)
        data = await client.async_update()
        await client.close()  # must not close the caller's connection
        assert connection.connected
    finally:
        await connection.close()
    assert {k: str(v) for k, v in data.items()} == {k: str(v) for k, v in expected_library_values(PROFILE_615).items()}
    assert emu.connections == 1


def test_host_and_unit_are_mutually_exclusive(mock_modbus_unit):
    with pytest.raises(ValueError):
        ThermiaGenesis("localhost", unit=mock_modbus_unit, kind=WPE_I_KIND)
    with pytest.raises(ValueError):
        ThermiaGenesis(kind=WPE_I_KIND)


async def test_reads_through_a_mock_unit(mock_modbus_unit):
    """Fast unit-level check with modbus-connection's in-memory mock: no TCP at all."""
    mock_modbus_unit.load_raw({"input": {311: 6, 312: 1, 313: 5}})
    client = ThermiaGenesis(unit=mock_modbus_unit, kind=WPE_I_KIND, delay=0)
    data = await client.async_update(only_registers=FIRMWARE)
    assert [data[name] for name in FIRMWARE] == [6, 1, 5]
    assert client.firmware == "6.1.5"


async def test_unreachable_host_raises_connection_error():
    port = _closed_port()
    client = ThermiaGenesis("127.0.0.1", port=port, kind=WPE_I_KIND, delay=0, timeout=1)
    with pytest.raises(ThermiaConnectionError):
        await client.async_update(only_registers=FIRMWARE)
    await client.close()


async def test_harness_update_is_native_async(start_emulator):
    """library_full_update no longer needs a worker thread."""
    emu = await start_emulator(PROFILE_615)
    data = await library_full_update(emu)
    assert data["input_control_software_version_minor"] == 1


def _closed_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]  # nothing listens here once the socket closes
