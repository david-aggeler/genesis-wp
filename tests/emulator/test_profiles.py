"""Packaged firmware profiles: well-formed, identifier-free, and faithful to the captures."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from genesis_wp.const import MODEL_NAMES, MODEL_STIEBEL_WPE_I_071
from genesis_wp.emulator import ProfileError, get_profile, packaged_profiles
from genesis_wp.emulator.profile import Profile

from ..harness import PROFILE_606, PROFILE_615

pytestmark = pytest.mark.emulator

PROFILE_DIR = Path(__file__).parents[2] / "genesis_wp" / "emulator" / "profiles"
MODEL_KEY_ADDED_AFTER = "58b8739"  # last commit before change_request-15


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


# DHF-REQ: genesis-wp/requirement-8  DHF-TEST: genesis-wp/ac-34
@pytest.mark.parametrize("name", [PROFILE_606, PROFILE_615])
def test_profile_declares_its_library_model(name):
    assert get_profile(name).model == MODEL_STIEBEL_WPE_I_071
    assert get_profile(name).model in MODEL_NAMES


# DHF-REQ: genesis-wp/requirement-8  DHF-TEST: genesis-wp/ac-34
@pytest.mark.parametrize("model", [None, "", "stiebel_wpe_i", 7])
def test_profile_without_a_known_model_is_rejected(model):
    data = json.loads((PROFILE_DIR / f"{PROFILE_615}.json").read_text())
    if model is None:
        del data["model"]
    else:
        data["model"] = model
    with pytest.raises(ProfileError, match="model"):
        Profile.from_dict(data)


# change_request-15 added the model key in place: everything else is the capture, unchanged.
@pytest.mark.parametrize("path", sorted(PROFILE_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_model_key_is_the_only_change_since_capture(path):
    rel = path.relative_to(PROFILE_DIR.parents[2]).as_posix()
    try:
        before = subprocess.run(
            ["git", "show", f"{MODEL_KEY_ADDED_AFTER}:{rel}"],
            cwd=PROFILE_DIR.parents[2], capture_output=True, check=True, text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("no git history")
    now = json.loads(path.read_text())
    assert now.pop("model") == MODEL_STIEBEL_WPE_I_071
    assert now == json.loads(before)


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
