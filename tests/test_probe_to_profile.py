"""The probe converter rebuilds each packaged profile from its committed capture facts.

The capture's ``library_values.json`` is committed as a test fixture; ``summary.json`` is
not (it lives with the homelab probe), so its extra raw reads are rebuilt here from the
profile they produced. Guards ``scripts/probe_to_profile.py`` against conversion changes.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from genesis_wp.emulator.profile import packaged_profiles

from .harness import FIXTURES

pytestmark = pytest.mark.fixture

ROOT = Path(__file__).parent.parent
PROFILES_DIR = ROOT / "genesis_wp" / "emulator" / "profiles"
EXTRA_LENGTHS = {"dinput_247_254_ksm": 8, "input_413_fan_test": 1, "input_157_158_power": 2, "holding_0_operational_mode": 1}


def _converter():
    spec = importlib.util.spec_from_file_location("probe_to_profile", ROOT / "scripts" / "probe_to_profile.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _probe_dir(tmp_path: Path, converter, name: str, profile: dict) -> Path:
    values = profile["blocks"]
    extras = {
        label: {"raw": [values[block]["values"].get(str(first + i), 0) for i in range(EXTRA_LENGTHS[label])]}
        for label, (block, first) in converter.EXTRAS.items()
    }
    summary = {"kind": converter.KIND, "firmware": profile["firmware"]["software"], "extras": extras}
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    (tmp_path / "library_values.json").write_text((FIXTURES / "library_values" / f"{name}.json").read_text())
    return tmp_path


@pytest.mark.parametrize("name", packaged_profiles())
def test_capture_converts_to_the_packaged_profile(tmp_path, name):
    converter = _converter()
    packaged = json.loads((PROFILES_DIR / f"{name}.json").read_text())
    firmware = packaged["firmware"]
    supplements = sorted((ROOT / "scripts" / "probe_supplements").glob(f"{name}*.json"))
    probe = _probe_dir(tmp_path, converter, name, packaged)
    converted = converter.convert(probe, firmware["software"], firmware["controller"], packaged["source"]["captured"], supplements)
    assert converted == packaged


def test_unknown_summary_field_aborts(tmp_path):
    converter = _converter()
    name = packaged_profiles()[0]
    packaged = json.loads((PROFILES_DIR / f"{name}.json").read_text())
    probe = _probe_dir(tmp_path, converter, name, packaged)
    summary = json.loads((probe / "summary.json").read_text())
    (probe / "summary.json").write_text(json.dumps({**summary, "serial": "x"}))
    with pytest.raises(SystemExit, match="unknown fields"):
        converter.convert(probe, packaged["firmware"]["software"], "17.3.145", "2026-10-22")
