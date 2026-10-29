"""Emulator behavior against requirement-8 (genesis-wp emulator) and its acceptance criteria."""

from __future__ import annotations

import asyncio

import pytest
from pymodbus.constants import ExcCodes

from genesis_wp.emulator import (
    COILS,
    DISCRETE_INPUTS,
    HOLDING_REGISTERS,
    INPUT_REGISTERS,
    get_profile,
)

from ..harness import PROFILE_606, PROFILE_615, UNIT

pytestmark = pytest.mark.emulator

READERS = {
    COILS: "read_coils",
    DISCRETE_INPUTS: "read_discrete_inputs",
    HOLDING_REGISTERS: "read_holding_registers",
    INPUT_REGISTERS: "read_input_registers",
}


async def read(client, block: str, address: int, count: int = 1):
    response = await getattr(client, READERS[block])(address, count=count, device_id=UNIT)
    if response.isError():
        return response
    return response.bits[:count] if block in (COILS, DISCRETE_INPUTS) else response.registers


# DHF-REQ: genesis-wp/requirement-8  DHF-TEST: genesis-wp/ac-19
async def test_serves_every_profile_value(start_emulator, connect):
    emu = await start_emulator(PROFILE_615)
    client = await connect(emu)
    profile = get_profile(PROFILE_615)
    for name, block in profile.blocks.items():
        for start, end in block.ranges:
            got = await read(client, name, start, end - start + 1)
            expected = [block.value(a) for a in range(start, end + 1)]
            if block.is_bits:
                expected = [bool(v) for v in expected]
            assert got == expected, f"{name} {start}-{end}"


# DHF-REQ: genesis-wp/requirement-8  DHF-TEST: genesis-wp/ac-20
async def test_unimplemented_address_gets_illegal_data_address(start_emulator, connect):
    emu = await start_emulator()
    client = await connect(emu)
    # One address outside every range per block (input 3 sits between the answered 1-2 and 4-26).
    for block, address in ((COILS, 15), (DISCRETE_INPUTS, 61), (HOLDING_REGISTERS, 116), (INPUT_REGISTERS, 3)):
        response = await read(client, block, address)
        assert response.isError(), f"{block} {address}"
        assert response.exception_code == ExcCodes.ILLEGAL_ADDRESS


async def test_range_crossing_a_gap_is_rejected_whole(start_emulator, connect):
    emu = await start_emulator()
    client = await connect(emu)
    # coils 3-14 and 20-35 are answered, 15-19 are not; a read across both fails as a whole.
    response = await read(client, COILS, 10, 12)
    assert response.isError() and response.exception_code == ExcCodes.ILLEGAL_ADDRESS
    response = await read(client, DISCRETE_INPUTS, 245, 12)  # 235-254 answered, 255+ not
    assert response.isError() and response.exception_code == ExcCodes.ILLEGAL_ADDRESS


# DHF-REQ: genesis-wp/requirement-8  DHF-TEST: genesis-wp/ac-21
async def test_write_is_recorded(start_emulator, connect):
    emu = await start_emulator()
    client = await connect(emu)
    response = await client.write_register(27, 5, device_id=UNIT)
    assert not response.isError()
    assert [(w.function_code, w.address, w.values) for w in emu.writes] == [(6, 27, (5,))]


async def test_reads_are_logged_and_not_writes(start_emulator, connect):
    emu = await start_emulator()
    client = await connect(emu)
    await read(client, INPUT_REGISTERS, 311, 3)
    await read(client, COILS, 3, 4)
    assert [(r.function_code, r.address, r.count) for r in emu.requests] == [(4, 311, 3), (1, 3, 4)]
    assert emu.writes == []


async def test_writes_applied_by_default(start_emulator, connect):
    emu = await start_emulator()
    client = await connect(emu)
    await client.write_coil(8, True, device_id=UNIT)
    await client.write_register(27, 5, device_id=UNIT)
    assert await read(client, COILS, 8) == [True]
    assert await read(client, HOLDING_REGISTERS, 27) == [5]


async def test_ignore_writes_records_but_keeps_state(start_emulator, connect):
    emu = await start_emulator(apply_writes=False)
    client = await connect(emu)
    assert not (await client.write_coil(8, True, device_id=UNIT)).isError()
    assert not (await client.write_registers(26, [7, 7], device_id=UNIT)).isError()
    holding = emu.profile.blocks[HOLDING_REGISTERS]
    assert await read(client, COILS, 8) == [bool(emu.profile.blocks[COILS].value(8))]
    assert await read(client, HOLDING_REGISTERS, 26, 2) == [holding.value(26), holding.value(27)]
    assert [w.function_code for w in emu.writes] == [5, 16]


async def test_write_outside_profile_is_rejected_and_recorded(start_emulator, connect):
    emu = await start_emulator()
    client = await connect(emu)
    response = await client.write_register(116, 1, device_id=UNIT)
    assert response.isError() and response.exception_code == ExcCodes.ILLEGAL_ADDRESS
    assert emu.writes[0].rejected == ExcCodes.ILLEGAL_ADDRESS


# DHF-REQ: genesis-wp/requirement-8  DHF-TEST: genesis-wp/ac-22
async def test_two_instances_on_separate_ports(start_emulator, connect):
    old = await start_emulator(PROFILE_606)
    new = await start_emulator(PROFILE_615)
    assert old.port != new.port
    assert await read(await connect(old), INPUT_REGISTERS, 311, 3) == [6, 0, 6]
    assert await read(await connect(new), INPUT_REGISTERS, 311, 3) == [6, 1, 5]


# DHF-REQ: genesis-wp/requirement-8  DHF-TEST: genesis-wp/ac-23
async def test_dynamics_move_condenser_supply_within_bounds(start_emulator, connect):
    emu = await start_emulator(dynamics=True, seed=1)
    client = await connect(emu)
    spec = next(d for d in emu.profile.dynamics if d.name == "condenser_out_temperature")
    first = (await read(client, INPUT_REGISTERS, spec.address))[0]
    emu.step()
    second = (await read(client, INPUT_REGISTERS, spec.address))[0]
    assert first != second
    for value in (first, second):
        assert spec.min <= value <= spec.max  # positive temperatures: raw == signed


async def test_dynamics_are_reproducible_and_bounded(start_emulator, connect):
    a = await start_emulator(dynamics=True, seed=7)
    b = await start_emulator(dynamics=True, seed=7)
    ca, cb = await connect(a), await connect(b)
    for _ in range(200):
        a.step()
        b.step()
    for spec in a.profile.dynamics:
        va = (await read(ca, INPUT_REGISTERS, spec.address))[0]
        assert va == (await read(cb, INPUT_REGISTERS, spec.address))[0]
        signed = va - 0x10000 if spec.signed and va >= 0x8000 else va
        assert spec.min <= signed <= spec.max


async def test_dynamics_tick_on_their_own(start_emulator, connect):
    emu = await start_emulator(dynamics=True, dynamics_interval=0.02)
    client = await connect(emu)
    address = emu.profile.dynamics[0].address
    first = (await read(client, INPUT_REGISTERS, address))[0]
    for _ in range(50):
        await asyncio.sleep(0.02)
        if (await read(client, INPUT_REGISTERS, address))[0] != first:
            return
    raise AssertionError("dynamics ticker did not change the value")


async def test_dynamics_off_by_default(start_emulator, connect):
    emu = await start_emulator()
    client = await connect(emu)
    before = await read(client, INPUT_REGISTERS, 4, 23)
    emu.step(10)
    assert await read(client, INPUT_REGISTERS, 4, 23) == before


async def test_configuration_registers_do_not_move(start_emulator, connect):
    emu = await start_emulator(dynamics=True)
    client = await connect(emu)
    before = await read(client, HOLDING_REGISTERS, 0, 13)
    emu.step(50)
    assert await read(client, HOLDING_REGISTERS, 0, 13) == before


async def test_injected_fault_fails_only_that_range(start_emulator, connect):
    emu = await start_emulator()
    client = await connect(emu)
    emu.inject_fault(INPUT_REGISTERS, 8, 11)
    response = await read(client, INPUT_REGISTERS, 4, 16)
    assert response.isError() and response.exception_code == ExcCodes.DEVICE_FAILURE
    assert await read(client, INPUT_REGISTERS, 311, 3) == [6, 1, 5]
    emu.clear_faults()
    assert isinstance(await read(client, INPUT_REGISTERS, 4, 16), list)


async def test_holding_and_input_are_not_swapped(start_emulator, connect):
    """pymodbus 3.13.1's deprecated ModbusDeviceContext swaps these; SimDevice must not."""
    emu = await start_emulator()
    client = await connect(emu)
    profile = emu.profile
    shared = sorted(
        a for a in profile.blocks[HOLDING_REGISTERS].values
        if a in profile.blocks[INPUT_REGISTERS].values
        and profile.blocks[HOLDING_REGISTERS].value(a) != profile.blocks[INPUT_REGISTERS].value(a)
    )
    assert shared, "profile needs an address with different holding and input values"
    address = shared[0]
    assert await read(client, HOLDING_REGISTERS, address) == [profile.blocks[HOLDING_REGISTERS].value(address)]
    assert await read(client, INPUT_REGISTERS, address) == [profile.blocks[INPUT_REGISTERS].value(address)]


async def test_unsupported_function_is_rejected(start_emulator, connect):
    emu = await start_emulator()
    client = await connect(emu)
    response = await client.readwrite_registers(read_address=0, read_count=1, write_address=0, values=[1], device_id=UNIT)
    assert response.isError() and response.exception_code == ExcCodes.ILLEGAL_FUNCTION
