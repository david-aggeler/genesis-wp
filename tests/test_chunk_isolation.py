"""A failed read chunk loses only its own registers (requirement-4)."""

from __future__ import annotations

import pytest
from pymodbus.constants import ExcCodes

from genesis_wp import ThermiaConnectionError, ThermiaGenesis
from genesis_wp.const import KEY_ADDRESS, KEY_REG_TYPE, MODEL_STIEBEL_WPE_I_071, REG_INPUT, REGISTERS

from .harness import PROFILE_615, WPE_I_KIND, expected_library_values, library_full_update


async def _chunk_with(start_emulator, address: int):
    """The (start, count) of the library's input-register read that covers ``address``."""
    probe = await start_emulator(PROFILE_615)
    await library_full_update(probe)
    request = next(r for r in probe.requests if r.function_code == 4 and r.address <= address < r.address + r.count)
    return request.address, request.count


def _library_inputs_in(start: int, count: int) -> set[str]:
    return {
        name for name, meta in REGISTERS.items()
        if meta[KEY_REG_TYPE] == REG_INPUT and meta.get(MODEL_STIEBEL_WPE_I_071)
        and start <= meta[KEY_ADDRESS] < start + count
    }


async def _faulted_update(start_emulator, code: ExcCodes):
    start, count = await _chunk_with(start_emulator, 9)  # the chunk with condenser out (input 9)
    emu = await start_emulator(PROFILE_615)
    emu.inject_fault("input_registers", start, start + count - 1, code)
    client = ThermiaGenesis(emu.host, port=emu.port, kind=WPE_I_KIND, delay=0)
    try:
        data = await client.async_update()
    finally:
        await client.close()
    return client, data, _library_inputs_in(start, count)


# DHF-REQ: genesis-wp/requirement-4  DHF-TEST: genesis-wp/ac-10
@pytest.mark.parametrize("code", [ExcCodes.DEVICE_FAILURE, ExcCodes.ILLEGAL_ADDRESS])
async def test_other_chunks_still_return_when_one_chunk_fails(start_emulator, code):
    client, data, in_chunk = await _faulted_update(start_emulator, code)
    expected = expected_library_values(PROFILE_615)
    outside = {name: value for name, value in expected.items() if name not in in_chunk}
    assert in_chunk and outside
    assert {k: str(v) for k, v in data.items()} == {k: str(v) for k, v in outside.items()}


# DHF-REQ: genesis-wp/requirement-4  DHF-TEST: genesis-wp/ac-11
async def test_failed_chunk_registers_are_reported_as_unread(start_emulator):
    client, data, in_chunk = await _faulted_update(start_emulator, ExcCodes.DEVICE_FAILURE)
    assert client.unread == in_chunk
    assert not in_chunk & set(data)  # absent, not stale


async def test_unread_registers_are_not_kept_from_a_previous_update(start_emulator):
    start, count = await _chunk_with(start_emulator, 9)
    emu = await start_emulator(PROFILE_615)
    client = ThermiaGenesis(emu.host, port=emu.port, kind=WPE_I_KIND, delay=0)
    try:
        first = await client.async_update()
        assert "input_condenser_out_temperature" in first and client.unread == set()
        emu.inject_fault("input_registers", start, start + count - 1)
        second = await client.async_update()
    finally:
        await client.close()
    assert "input_condenser_out_temperature" not in second
    assert "input_condenser_out_temperature" not in client.data
    assert "input_condenser_out_temperature" in client.unread


async def test_a_lost_connection_still_fails_the_whole_update(start_emulator):
    emu = await start_emulator(PROFILE_615)
    client = ThermiaGenesis(emu.host, port=emu.port, kind=WPE_I_KIND, delay=0, timeout=1)
    await client.async_update(only_registers=["input_outdoor_temperature"])
    await emu.stop()
    with pytest.raises(ThermiaConnectionError):
        await client.async_update()
    await client.close()
