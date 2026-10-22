"""Packaged firmware profiles: well-formed, identifier-free, and faithful to the captures."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from genesis_wp.emulator import Emulator, ProfileError, get_profile, packaged_profiles
from genesis_wp.emulator.profile import Profile

from .conftest import PROFILE_606, PROFILE_615

FIXTURES = Path(__file__).parent.parent / "fixtures" / "library_values"
PROFILE_DIR = Path(__file__).parents[2] / "genesis_wp" / "emulator" / "profiles"


def test_packaged_profiles():
    assert packaged_profiles() == [PROFILE_606, PROFILE_615]


@pytest.mark.parametrize("name", [PROFILE_606, PROFILE_615])
def test_profile_loads_and_versions_match_name(name):
    profile = get_profile(name)
    software = name.rsplit("-", 1)[1]
    assert profile.firmware["software"] == software
    inputs = profile.blocks["input_registers"]
    assert ".".join(str(inputs.value(a)) for a in (311, 312, 313)) == software
    assert ".".join(str(inputs.value(a)) for a in (57, 58, 59)) == profile.firmware["controller"]


@pytest.mark.parametrize("path", sorted(PROFILE_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_profile_has_no_identifiers(path):
    text = path.read_text().lower()
    for pattern in (r"serial", r"\bhost", r"\.local\b", r"aggeler", r"\b\d{1,3}(\.\d{1,3}){3}\b"):
        assert not re.search(pattern, text), f"{path.name}: matches {pattern}"


def test_firmware_update_changed_only_holding_27():
    old, new = get_profile(PROFILE_606), get_profile(PROFILE_615)
    assert old.blocks["holding_registers"].ranges == new.blocks["holding_registers"].ranges
    changed = {
        a for a in old.blocks["holding_registers"].values
        if old.blocks["holding_registers"].value(a) != new.blocks["holding_registers"].value(a)
    }
    assert changed == {27}


def test_invalid_profiles_are_rejected():
    good = json.loads((PROFILE_DIR / f"{PROFILE_615}.json").read_text())
    with pytest.raises(ProfileError):
        Profile.from_dict({**good, "format": "other"})
    broken = json.loads(json.dumps(good))
    broken["blocks"]["coils"]["values"]["999"] = 1
    with pytest.raises(ProfileError):
        Profile.from_dict(broken)
    with pytest.raises(ProfileError):
        get_profile("no-such-profile")


@pytest.mark.parametrize("name", [PROFILE_606, PROFILE_615])
async def test_inherited_library_decodes_the_captured_values(name):
    """Round trip: the probe's own library, reading the emulator, decodes exactly what it
    decoded from the real unit, in the same number of requests, with no rejection."""
    from genesis_wp import ThermiaGenesis

    expected = {k: v for k, v in json.loads((FIXTURES / f"{name}.json").read_text()).items() if v is not None}
    async with Emulator(get_profile(name), port=0) as emu:

        def update():
            hp = ThermiaGenesis(emu.host, port=emu.port, kind="stiebel_wpe_i_07_1", delay=0, max_registers=16)
            return asyncio.run(hp.async_update())

        got = await asyncio.to_thread(update)  # pyModbusTCP is blocking
    assert len(emu.requests) == 35
    assert not [r for r in emu.requests if r.rejected]
    assert {k: str(v) for k, v in got.items()} == {k: str(v) for k, v in expected.items()}
