"""Emulator room set points shift the heating curves (requirement-23, formal_review-3).

Every write here goes to the emulator only, never to the real plant (design_decision-5).
"""

from __future__ import annotations

import pytest

from genesis_wp.emulator import ProfileError

from ..harness import PROFILE_606, PROFILE_615, UNIT

HC1 = tuple(range(6, 13))  # holding 6-12
HC2 = tuple(range(109, 116))  # holding 109-115
HC1_AT_23 = (28, 33, 35, 38, 40, 45, 49)  # HC1 curve at 23.0 °C (formal_review-1)


def raw(celsius: float) -> int:
    return round(celsius * 100) & 0xFFFF


async def holding(client, addresses) -> tuple[float, ...]:
    start, count = addresses[0], len(addresses)
    response = await client.read_holding_registers(start, count=count, device_id=UNIT)
    assert not response.isError()
    return tuple(v / 100 for v in response.registers)


async def all_holding(emu, client) -> dict[int, int]:
    """Every served holding address and its raw value, read in chunks."""
    values = {}
    for start, end in emu.profile.blocks["holding_registers"].ranges:
        for chunk in range(start, end + 1, 100):
            count = min(100, end + 1 - chunk)
            response = await client.read_holding_registers(chunk, count=count, device_id=UNIT)
            assert not response.isError()
            values.update(zip(range(chunk, chunk + count), response.registers))
    return values


# DHF-REQ: genesis-wp/requirement-23  DHF-TEST: genesis-wp/ac-79
async def test_hc1_room_set_point_write_shifts_the_hc1_curve_2_k_per_k(start_emulator, connect):
    overrides = {("holding_registers", 5): raw(23.0)}
    overrides.update({("holding_registers", a): raw(v) for a, v in zip(HC1, HC1_AT_23)})
    emu = await start_emulator(PROFILE_615, overrides=overrides)
    client = await connect(emu)
    assert await holding(client, HC1) == HC1_AT_23
    assert not (await client.write_register(5, raw(22.0), device_id=UNIT)).isError()
    assert await holding(client, HC1) == (26, 31, 33, 36, 38, 43, 47)
    assert await holding(client, (5,)) == (22.0,)


# DHF-REQ: genesis-wp/requirement-23  DHF-TEST: genesis-wp/ac-80
async def test_measured_hc2_sequence_reproduces_the_plant_curve(start_emulator, connect):
    emu = await start_emulator(PROFILE_615)
    client = await connect(emu)
    assert emu.room_set_point_hc2 == 21.0
    assert await holding(client, HC2) == (23, 28, 30, 33, 35, 40, 44)
    sequence = [(22.5, 25), (19.5, 22), (21.0, 24), (21.5, 24), (22.0, 25), (25.0, 28),
                (24.0, 27), (24.5, 27), (25.0, 28), (21.0, 24), (21.5, 24)]
    for set_point, first in sequence:
        emu.set_room_set_point_hc2(set_point)
        assert emu.room_set_point_hc2 == set_point
        curve = await holding(client, HC2)
        assert curve[0] == first, f"after {set_point}"
        assert tuple(p - curve[0] for p in curve) == (0, 5, 7, 10, 12, 17, 21)


# DHF-REQ: genesis-wp/requirement-23  DHF-TEST: genesis-wp/ac-81
@pytest.mark.parametrize(("set_point", "shift", "reference"), [(21.5, 0, 21.0), (22.5, 2, 22.5), (23.5, 2, 23.5)])
async def test_hc2_shift_rounds_half_to_even(start_emulator, connect, set_point, shift, reference):
    emu = await start_emulator(PROFILE_615)
    client = await connect(emu)
    before = await holding(client, HC2)
    emu.set_room_set_point_hc2(set_point)
    after = await holding(client, HC2)
    assert tuple(a - b for a, b in zip(after, before)) == (shift,) * 7
    assert emu.heating.room_set_points["hc2"].reference == reference


# DHF-REQ: genesis-wp/requirement-23  DHF-TEST: genesis-wp/ac-82
async def test_hc2_room_set_point_touches_only_holding_109_to_115(start_emulator, connect):
    emu = await start_emulator(PROFILE_615)
    client = await connect(emu)
    before = await all_holding(emu, client)
    emu.set_room_set_point_hc2(23.0)
    after = await all_holding(emu, client)
    assert {a for a in before if before[a] != after[a]} == set(HC2)


# DHF-REQ: genesis-wp/requirement-23  DHF-TEST: genesis-wp/ac-83
async def test_profile_without_room_set_point_data_shifts_nothing(start_emulator, connect):
    emu = await start_emulator(PROFILE_606)
    client = await connect(emu)
    curve = await holding(client, HC1)
    assert not (await client.write_register(5, raw(25.0), device_id=UNIT)).isError()
    assert await holding(client, (5,)) == (25.0,)
    assert await holding(client, HC1) == curve
    with pytest.raises(ProfileError, match="room_set_points"):
        emu.set_room_set_point_hc2(22.0)


async def test_hc2_room_set_point_shifts_the_supply_target(start_emulator):
    emu = await start_emulator(PROFILE_615, dynamics=True, outdoor_temperature=0.0)
    emu.step()
    before = emu.heating.set_point_hc2
    emu.set_room_set_point_hc2(23.0)
    emu.step()
    assert emu.heating.set_point_hc2 == pytest.approx(before + 2)


def test_unknown_rounding_is_rejected():
    from genesis_wp.emulator.profile import RoomSetPointRule

    with pytest.raises(ProfileError, match="rounding"):
        RoomSetPointRule.from_dict({"factor": 1.0, "rounding": "up"})
