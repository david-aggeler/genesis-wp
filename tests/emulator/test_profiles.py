"""Packaged firmware profiles: well-formed, identifier-free, and faithful to the captures."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from genesis_wp.emulator import ProfileError, get_profile, packaged_profiles
from genesis_wp.emulator.profile import Profile

from ..harness import PROFILE_606, PROFILE_615

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
