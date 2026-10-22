"""WPE-I model covers the doc- and probe-verified registers (requirement-5, ac-12).

Inputs 2 (status bits) and 30 (calculated demand) are still missing: neither firmware
capture read them (see change_request-8, open data gap).
"""

from __future__ import annotations

import pytest

from genesis_wp.const import MODEL_STIEBEL_WPE_I_071, REGISTERS

from .harness import ADDED_WPE_I_REGISTERS, PROFILES, library_full_update

KSM = [f"dinput_ksm_{suffix}" for suffix in (
    "internal_error_1", "internal_error_2", "fan_test_pressure_sensor_before_start",
    "fan_test_pressure_sensor_not_triggered", "refrigerant_leak_detected",
    "refrigerant_leak_sensor_error", "communication_error", "fan_test_failed",
)]


def test_added_registers_are_enabled_for_the_wpe_i():
    assert set(KSM) <= set(ADDED_WPE_I_REGISTERS)
    for name in ADDED_WPE_I_REGISTERS:
        assert REGISTERS[name][MODEL_STIEBEL_WPE_I_071], name


# DHF-REQ: genesis-wp/requirement-5  DHF-TEST: genesis-wp/ac-12 (KSM, 413, 57-59; inputs 2 and 30 pending)
@pytest.mark.parametrize("profile", PROFILES)
async def test_wpe_i_update_reads_the_added_registers(start_emulator, profile):
    emu = await start_emulator(profile)
    data = await library_full_update(emu)
    for name, (block, address) in ADDED_WPE_I_REGISTERS.items():
        raw = emu.profile.blocks[block].value(address)
        expected = bool(raw) if block == "discrete_inputs" else raw
        assert data[name] == expected, name
    assert [r for r in emu.requests if r.rejected] == []


@pytest.mark.parametrize("profile", PROFILES)
async def test_controller_version_is_inputs_57_to_59(start_emulator, profile):
    emu = await start_emulator(profile)
    data = await library_full_update(emu)
    version = ".".join(str(data[f"input_software_version_{p}"]) for p in ("major", "minor", "micro"))
    assert version == emu.profile.firmware["controller"]  # 17.2.40 / 17.3.145
