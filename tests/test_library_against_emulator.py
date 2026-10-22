"""Full library updates against the emulator, one per captured firmware (requirement-7).

The canonical regression net: whatever the library's transport, a full WPE-I update
against each firmware profile must decode exactly what the library decoded from the real
unit, in the same requests, with nothing rejected.
"""

from __future__ import annotations

import pytest

from .harness import PROFILES, expected_library_values, library_full_update

# What the inherited library sent to the real unit during the probes (max_registers=16).
PROBE_REQUESTS = 35


# DHF-REQ: genesis-wp/requirement-7  DHF-TEST: genesis-wp/ac-18
@pytest.mark.parametrize("profile", PROFILES)
async def test_full_update_decodes_the_captured_values(start_emulator, profile):
    emu = await start_emulator(profile)
    got = await library_full_update(emu)
    expected = expected_library_values(profile)
    assert {k: str(v) for k, v in got.items()} == {k: str(v) for k, v in expected.items()}


@pytest.mark.parametrize("profile", PROFILES)
async def test_full_update_makes_the_probe_requests_and_nothing_is_rejected(start_emulator, profile):
    emu = await start_emulator(profile)
    await library_full_update(emu)
    assert len(emu.requests) == PROBE_REQUESTS
    assert [r for r in emu.requests if r.rejected] == []
    assert all(r.function_code in (1, 2, 3, 4) for r in emu.requests)


@pytest.mark.parametrize("profile", PROFILES)
async def test_full_update_reports_the_profile_firmware(start_emulator, profile):
    emu = await start_emulator(profile)
    got = await library_full_update(emu)
    software = ".".join(str(got[f"input_control_software_version_{p}"]) for p in ("major", "minor", "micro"))
    assert software == emu.profile.firmware["software"]


async def test_second_full_update_on_the_same_client(start_emulator):
    """Inherited bug: the holding-116 skip check raised KeyError on every update after the first."""
    from genesis_wp import ThermiaGenesis

    from .harness import PROFILE_615, WPE_I_KIND

    emu = await start_emulator(PROFILE_615)
    client = ThermiaGenesis(emu.host, port=emu.port, kind=WPE_I_KIND, delay=0, max_registers=16)
    try:
        first = await client.async_update()
        second = await client.async_update()
    finally:
        await client.close()
    assert second == first
    assert len(emu.requests) == 2 * PROBE_REQUESTS
