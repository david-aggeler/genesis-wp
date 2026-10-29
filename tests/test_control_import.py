"""The control client imports without pymodbus and Home Assistant (requirement-24)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BLOCKED = '''
import sys

class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("pymodbus", "homeassistant"):
            raise ImportError(f"blocked: {name}")
        return None

sys.meta_path.insert(0, Block())
import genesis_wp.emulator.control as control
assert "pymodbus" not in sys.modules and "homeassistant" not in sys.modules
print(control.EmulatorControl.__name__)
'''


# DHF-REQ: genesis-wp/requirement-24  DHF-TEST: genesis-wp/ac-92
def test_control_client_imports_without_pymodbus_and_home_assistant():
    done = subprocess.run(
        [sys.executable, "-c", BLOCKED], cwd=Path(__file__).resolve().parent.parent,
        capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "EmulatorControl"
