"""WPE-I model covers the doc- and probe-verified registers (requirement-5, ac-12).

Inputs 2 (status bits) and 30 (calculated demand) come from separate read-only captures
recorded as profile supplements (6.1.5: 2026-10-22 22:20; 6.0.6: owner's read 2026-10-18).
"""

from __future__ import annotations

import pytest

from genesis_wp.const import MODEL_STIEBEL_WPE_I_071, REGISTERS

from .harness import ADDED_WPE_I_REGISTERS, PROFILE_615, PROFILES, decode_raw, library_full_update

KSM = [f"dinput_ksm_{suffix}" for suffix in (
    "internal_error_1", "internal_error_2", "fan_test_pressure_sensor_before_start",
    "fan_test_pressure_sensor_not_triggered", "refrigerant_leak_detected",
    "refrigerant_leak_sensor_error", "communication_error", "fan_test_failed",
)]


def test_added_registers_are_enabled_for_the_wpe_i():
    assert set(KSM) <= set(ADDED_WPE_I_REGISTERS)
    for name in ADDED_WPE_I_REGISTERS:
        assert REGISTERS[name][MODEL_STIEBEL_WPE_I_071], name


# DHF-REQ: genesis-wp/requirement-5  DHF-TEST: genesis-wp/ac-12
@pytest.mark.parametrize("profile", PROFILES)
async def test_wpe_i_update_reads_the_added_registers(start_emulator, profile):
    emu = await start_emulator(profile)
    data = await library_full_update(emu)
    for name, (block, address) in ADDED_WPE_I_REGISTERS.items():
        assert data[name] == decode_raw(name, emu.profile.blocks[block].value(address)), name
    assert [r for r in emu.requests if r.rejected] == []


@pytest.mark.parametrize("profile", PROFILES)
async def test_controller_version_is_inputs_57_to_59(start_emulator, profile):
    emu = await start_emulator(profile)
    data = await library_full_update(emu)
    version = ".".join(str(data[f"input_software_version_{p}"]) for p in ("major", "minor", "micro"))
    assert version == emu.profile.firmware["controller"]  # 17.2.40 / 17.3.145


async def test_status_bits_and_demand_on_6_1_5(start_emulator):
    """Read 2026-10-22 22:20: operating state Heat (4), status bit 3 'heat' (8), demand 0.71."""
    emu = await start_emulator(PROFILE_615)
    data = await library_full_update(emu)
    assert data["input_status_bits"] == 8
    assert data["input_calculated_heating_demand"] == 0.71


async def test_input_30_is_read_alone(start_emulator):
    """Inputs 31-35 are not in the Stiebel document: input 30 gets its own request."""
    emu = await start_emulator(PROFILE_615)
    await library_full_update(emu)
    reads = [(r.function_code, r.address, r.count) for r in emu.requests]
    assert (4, 30, 1) in reads
    assert not [r for r in reads if r[0] == 4 and r[1] <= 31 and r[1] + r[2] - 1 >= 31 and r[1] != 31]
    assert not [r for r in reads if r[0] == 4 and 31 <= r[1] <= 35]


# DHF-REQ: genesis-wp/requirement-10  DHF-TEST: genesis-wp/ac-28
def test_every_wpe_i_register_declares_its_source():
    from genesis_wp.const import (
        KEY_ADDRESS, KEY_REG_TYPE, KEY_SOURCE, KEY_SOURCE_REF, SOURCE_MODEL_DOC, SOURCE_PLATFORM_DOC,
        SOURCE_PROBE,
    )

    from .register_sources import STIEBEL_WPE_I_071, THERMIA_GENESIS_17_1

    model = MODEL_STIEBEL_WPE_I_071
    counts = dict.fromkeys((SOURCE_MODEL_DOC, SOURCE_PLATFORM_DOC, SOURCE_PROBE), 0)
    for name, meta in REGISTERS.items():
        if not meta[model]:
            assert model not in meta.get(KEY_SOURCE, {}), name
            continue
        block, address = meta[KEY_REG_TYPE], meta[KEY_ADDRESS]
        source = meta.get(KEY_SOURCE, {}).get(model)
        if address in STIEBEL_WPE_I_071[block]:
            expected = SOURCE_MODEL_DOC
        elif address in THERMIA_GENESIS_17_1[block]:
            expected = SOURCE_PLATFORM_DOC
        else:
            expected = SOURCE_PROBE
        assert source == expected, name
        if source == SOURCE_PROBE:
            assert meta.get(KEY_SOURCE_REF, {}).get(model), f"{name}: probe entry names no capture"
        counts[source] += 1
    # Counts at 3753fa3 (issue-4): 87 registers outside the Stiebel doc, 84 of them in Thermia's.
    assert counts == {SOURCE_MODEL_DOC: 181, SOURCE_PLATFORM_DOC: 84, SOURCE_PROBE: 3}


# DHF-REQ: genesis-wp/requirement-5  DHF-TEST: genesis-wp/ac-13
def test_holding_307_308_311_312_are_mega_only_at_thermia_scale():
    from genesis_wp.const import KEY_ADDRESS, KEY_REG_TYPE, KEY_SCALE, MODEL_MEGA, REG_HOLDING

    entries = {m[KEY_ADDRESS]: m for m in REGISTERS.values() if m[KEY_REG_TYPE] == REG_HOLDING and m[KEY_ADDRESS] in (307, 308, 311, 312)}
    assert set(entries) == {307, 308, 311, 312}
    for address, meta in entries.items():
        assert meta[KEY_SCALE] == 100, address
        assert meta[MODEL_MEGA] and not meta[MODEL_STIEBEL_WPE_I_071], address
