"""Read-only by default: no write reaches the device without an explicit opt-in (requirement-1)."""

from __future__ import annotations

import pytest

from genesis_wp import GenesisReadOnlyError, ThermiaGenesis
from genesis_wp.const import (
    ATTR_COIL_ENABLE_TAP_WATER,
    ATTR_HOLDING_COMFORT_WHEEL_SETTING,
    REG_COIL,
    REG_HOLDING,
    REGISTERS,
    KEY_REG_TYPE,
    MODEL_STIEBEL_WPE_I_071,
)

from .harness import PROFILE_615, WPE_I_KIND, library_full_update

pytestmark = pytest.mark.writes

WRITABLE = sorted(
    name for name, meta in REGISTERS.items()
    if meta[KEY_REG_TYPE] in (REG_COIL, REG_HOLDING) and meta.get(MODEL_STIEBEL_WPE_I_071)
)


# DHF-REQ: genesis-wp/requirement-1  DHF-TEST: genesis-wp/ac-1
@pytest.mark.parametrize("register", [ATTR_COIL_ENABLE_TAP_WATER, ATTR_HOLDING_COMFORT_WHEEL_SETTING])
async def test_default_client_refuses_a_write_before_sending(start_emulator, register):
    emu = await start_emulator(PROFILE_615)
    client = ThermiaGenesis(emu.host, port=emu.port, kind=WPE_I_KIND, delay=0)
    with pytest.raises(GenesisReadOnlyError):
        await client.async_set(register, 1)
    await client.close()
    assert emu.writes == []
    assert emu.requests == []  # refused before any frame, not even a read
    assert emu.connections == 0  # and before connecting


async def test_every_writable_register_is_refused_by_default(mock_modbus_unit):
    writes = []
    mock_modbus_unit.on_write(writes.append)
    client = ThermiaGenesis(unit=mock_modbus_unit, kind=WPE_I_KIND, delay=0)
    assert client.read_only
    assert WRITABLE
    for register in WRITABLE:
        with pytest.raises(GenesisReadOnlyError):
            await client.async_set(register, 1)
    assert writes == []


# DHF-REQ: genesis-wp/requirement-1  DHF-TEST: genesis-wp/ac-2
async def test_full_update_sends_only_read_function_codes(start_emulator):
    emu = await start_emulator(PROFILE_615)
    await library_full_update(emu)
    assert emu.requests
    assert {r.function_code for r in emu.requests} <= {1, 2, 3, 4}


# DHF-REQ: genesis-wp/requirement-1  DHF-TEST: genesis-wp/ac-3
async def test_explicit_opt_in_lets_a_write_through(start_emulator):
    emu = await start_emulator(PROFILE_615)
    client = ThermiaGenesis(emu.host, port=emu.port, kind=WPE_I_KIND, delay=0, read_only=False)
    await client.async_set(ATTR_HOLDING_COMFORT_WHEEL_SETTING, 21)
    await client.close()
    assert [(w.function_code, w.address, w.values, w.rejected) for w in emu.writes] == [
        (6, REGISTERS[ATTR_HOLDING_COMFORT_WHEEL_SETTING]["address"], (21 * REGISTERS[ATTR_HOLDING_COMFORT_WHEEL_SETTING]["scale"],), None)
    ]


def test_read_only_cannot_be_changed_after_construction(mock_modbus_unit):
    client = ThermiaGenesis(unit=mock_modbus_unit, kind=WPE_I_KIND)
    with pytest.raises(AttributeError):
        client.read_only = False
