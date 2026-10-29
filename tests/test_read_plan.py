"""Read planning never spans a register the client skips or a profile gap (issue-7, requirement-4).

Holding 116 (fixed system supply set point) is read only while coil 41 enables it; the
captured profiles refuse it. Addresses the read-only probes never read successfully are
gaps in the profiles' ranges; the WPE-I range table must not span them.
"""

from __future__ import annotations

import itertools

import pytest

from genesis_wp import ThermiaGenesis
from genesis_wp.const import (
    ATTR_COIL_ENABLE_FIXED_SYSTEM_SUPPLY_SET_POINT,
    ATTR_HOLDING_FIXED_SYSTEM_SUPPLY_SET_POINT,
    KEY_ADDRESS,
    KEY_REG_TYPE,
    MODEL_STIEBEL_WPE_I_071,
    REG_COIL,
    REG_DISCRETE_INPUT,
    REG_HOLDING,
    REG_INPUT,
    REGISTER_RANGES,
    REGISTERS,
)
from genesis_wp.emulator import ProfileError, get_profile

from .harness import PROFILE_615, PROFILES, WPE_I_KIND, decode_raw

pytestmark = pytest.mark.fixture

BLOCK_OF = {
    REG_COIL: "coils",
    REG_DISCRETE_INPUT: "discrete_inputs",
    REG_INPUT: "input_registers",
    REG_HOLDING: "holding_registers",
}
GATED = (REG_HOLDING, 116)  # read alone, only while coil 41 is on


def _client(emu) -> ThermiaGenesis:
    return ThermiaGenesis(emu.host, port=emu.port, kind=WPE_I_KIND, delay=0, max_registers=16)


def _covers_116(request) -> bool:
    return request.function_code == 3 and request.address <= 116 <= request.address + request.count - 1


# DHF-REQ: genesis-wp/requirement-4  DHF-TEST: genesis-wp/ac-31
@pytest.mark.parametrize("selected", [True, False], ids=["selected", "full"])
async def test_no_request_spans_skipped_holding_116(start_emulator, selected):
    emu = await start_emulator(PROFILE_615)
    only = None
    if selected:
        only = [
            name for name, meta in REGISTERS.items()
            if meta[MODEL_STIEBEL_WPE_I_071] and meta[KEY_REG_TYPE] in (REG_HOLDING, REG_COIL)
        ]
    client = _client(emu)
    try:
        data = await client.async_update(only_registers=only)
        unread = set(client.unread)
    finally:
        await client.close()
    assert data[ATTR_COIL_ENABLE_FIXED_SYSTEM_SUPPLY_SET_POINT] is False
    assert not [r for r in emu.requests if _covers_116(r)]
    assert [r for r in emu.requests if r.rejected] == []
    assert unread == set()


# DHF-REQ: genesis-wp/requirement-4  DHF-TEST: genesis-wp/ac-31
@pytest.mark.parametrize("profile", PROFILES)
async def test_any_single_register_selection_is_served(start_emulator, profile):
    """Each enabled register read on its own next to its neighbors: a selection never spans a gap."""
    emu = await start_emulator(profile)
    enabled = [n for n, m in REGISTERS.items() if m[MODEL_STIEBEL_WPE_I_071] and n != ATTR_HOLDING_FIXED_SYSTEM_SUPPLY_SET_POINT]
    client = _client(emu)
    try:
        # Every second register: the planner must still build chunks only inside served ranges.
        for only in (enabled[::2], enabled[1::2]):
            await client.async_update(only_registers=only)
            assert client.unread == set()
    finally:
        await client.close()
    assert [r for r in emu.requests if r.rejected] == []


# DHF-REQ: genesis-wp/requirement-4  DHF-TEST: genesis-wp/ac-32
async def test_holding_116_is_read_alone_once_coil_41_enables_it(start_emulator):
    raw = 350
    emu = await start_emulator(PROFILE_615, overrides={("coils", 41): 1, ("holding_registers", 116): raw})
    client = _client(emu)
    try:
        first = await client.async_update()
        assert ATTR_HOLDING_FIXED_SYSTEM_SUPPLY_SET_POINT not in first
        assert first[ATTR_COIL_ENABLE_FIXED_SYSTEM_SUPPLY_SET_POINT] is True
        before = len(emu.requests)
        second = await client.async_update()
        unread = set(client.unread)
    finally:
        await client.close()
    reads = [(r.function_code, r.address, r.count) for r in emu.requests[before:]]
    assert (3, 116, 1) in reads
    assert [r for r in emu.requests[before:] if _covers_116(r)] == [emu.requests[before + reads.index((3, 116, 1))]]
    assert second[ATTR_HOLDING_FIXED_SYSTEM_SUPPLY_SET_POINT] == decode_raw(ATTR_HOLDING_FIXED_SYSTEM_SUPPLY_SET_POINT, raw)
    assert [r for r in emu.requests if r.rejected] == []
    assert unread == set()


# DHF-REQ: genesis-wp/requirement-4  DHF-TEST: genesis-wp/ac-33
def test_wpe_i_read_ranges_stay_inside_the_profiles():
    ranges = REGISTER_RANGES[MODEL_STIEBEL_WPE_I_071]
    profiles = [get_profile(p) for p in PROFILES]
    planned = [
        (reg_type, block, start, end)
        for reg_type, block in BLOCK_OF.items()
        for start, end in ranges[reg_type]
        if not ((reg_type, start) == GATED and start == end)
    ]
    for (reg_type, block, start, end), profile in itertools.product(planned, profiles):
        assert profile.blocks[block].covers(start, end - start + 1), (profile.name, reg_type, start, end)
    for name, meta in REGISTERS.items():
        if meta[MODEL_STIEBEL_WPE_I_071]:
            address = meta[KEY_ADDRESS]
            assert any(s <= address <= e for s, e in ranges[meta[KEY_REG_TYPE]]), name
    assert [116, 116] in ranges[REG_HOLDING]


# ---- emulator overrides -----------------------------------------------------------


def test_override_sets_a_value_inside_a_range():
    profile = get_profile(PROFILE_615)
    changed = profile.with_overrides({("coils", 41): 1})
    assert changed.blocks["coils"].value(41) == 1
    assert changed.blocks["coils"].ranges == profile.blocks["coils"].ranges
    assert profile.blocks["coils"].value(41) == 0  # the loaded profile is untouched


def test_override_outside_every_range_adds_a_single_address_range():
    changed = get_profile(PROFILE_615).with_overrides({("holding_registers", 116): 350})
    block = changed.blocks["holding_registers"]
    assert block.covers(116, 1) and block.value(116) == 350
    assert not block.covers(105, 16)  # spanning it from a neighbor is still refused


@pytest.mark.parametrize("overrides", [
    {("pool", 1): 1},
    {("coils", 41): 2},
    {("holding_registers", 116): 0x10000},
])
def test_override_rejects_unknown_blocks_and_out_of_range_values(overrides):
    with pytest.raises(ProfileError):
        get_profile(PROFILE_615).with_overrides(overrides)
