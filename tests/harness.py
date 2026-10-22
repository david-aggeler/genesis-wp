"""Shared test harness: firmware profiles, expected values, and a full library update.

Device state comes only from the emulator's packaged profiles (requirement-7). The
expected decoded values in ``tests/fixtures/library_values/`` are what the library
decoded from the real unit during the 2026-10-22 read-only probes.
"""

from __future__ import annotations

import json
from pathlib import Path

from genesis_wp.emulator import Emulator

PROFILE_606 = "stiebel-wpe-i-07.1-plus-6.0.6"  # software 6.0.6 / Genesis 17.2.40
PROFILE_615 = "stiebel-wpe-i-07.1-plus-6.1.5"  # software 6.1.5 / Genesis 17.3.145
PROFILES = (PROFILE_606, PROFILE_615)
UNIT = 1
WPE_I_KIND = "stiebel_wpe_i_07_1"

FIXTURES = Path(__file__).parent / "fixtures"


def expected_library_values(profile: str) -> dict:
    """Decoded values the library produced from the real unit for this firmware."""
    raw = json.loads((FIXTURES / "library_values" / f"{profile}.json").read_text())
    return {name: value for name, value in raw.items() if value is not None}


async def library_full_update(emu: Emulator) -> dict:
    """Run a full WPE-I update of the genesis_wp library against an emulator.

    The library is async on modbus-connection, so the update runs on the same event
    loop as the emulator.
    """
    from genesis_wp import ThermiaGenesis

    client = ThermiaGenesis(emu.host, port=emu.port, kind=WPE_I_KIND, delay=0, max_registers=16)
    try:
        return await client.async_update()
    finally:
        await client.close()
