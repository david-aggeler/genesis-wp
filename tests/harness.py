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


# Sensors the owner's unit does not have fitted (installer wiring: T34 room, T36/T37 tap
# water, no pool, no cooling circuit) that the Stiebel document declares a "not fitted"
# sentinel for. The capture decoded them as 200.0; the library now reports None.
NOT_FITTED_ON_UNIT = frozenset({
    "input_tap_water_top_temperature",
    "input_tap_water_lower_temperature",
    "input_tap_water_weighted_temperature",
    "input_pool_supply_line_temperature",
    "input_pool_return_line_temperature",
    "input_cooling_circuit_supply_line_temperature",
    "input_room_temperature_sensor",
})


# Registers added to the WPE-I model after the capture (change_request-8): name -> (block, address).
# Expected values are the profile's raw values decoded with the register's scale and sign.
ADDED_WPE_I_REGISTERS = {
    "dinput_ksm_internal_error_1": ("discrete_inputs", 247),
    "dinput_ksm_internal_error_2": ("discrete_inputs", 248),
    "dinput_ksm_fan_test_pressure_sensor_before_start": ("discrete_inputs", 249),
    "dinput_ksm_fan_test_pressure_sensor_not_triggered": ("discrete_inputs", 250),
    "dinput_ksm_refrigerant_leak_detected": ("discrete_inputs", 251),
    "dinput_ksm_refrigerant_leak_sensor_error": ("discrete_inputs", 252),
    "dinput_ksm_communication_error": ("discrete_inputs", 253),
    "dinput_ksm_fan_test_failed": ("discrete_inputs", 254),
    "input_ksm_fan_test_status": ("input_registers", 413),
    "input_software_version_major": ("input_registers", 57),
    "input_software_version_minor": ("input_registers", 58),
    "input_software_version_micro": ("input_registers", 59),
    "input_status_bits": ("input_registers", 2),
    "input_calculated_heating_demand": ("input_registers", 30),
}


def expected_library_values(profile: str) -> dict:
    """Decoded values the library is expected to produce for this firmware.

    The captured values the probe's library decoded from the real unit, with declared
    "not fitted" sensors as None instead of 200.0.
    """
    raw = json.loads((FIXTURES / "library_values" / f"{profile}.json").read_text())
    expected = {name: value for name, value in raw.items() if value is not None}
    for name in NOT_FITTED_ON_UNIT:
        assert expected[name] == 200.0, f"{name}: capture no longer reads the sentinel"
        expected[name] = None
    from genesis_wp.emulator import get_profile

    blocks = get_profile(profile).blocks
    for name, (block, address) in ADDED_WPE_I_REGISTERS.items():
        expected[name] = decode_raw(name, blocks[block].value(address))
    return expected


def decode_raw(name: str, raw: int):
    """Reference decoding of one raw value for a register (bit, scale, int16 sign)."""
    from genesis_wp.const import KEY_DATATYPE, KEY_SCALE, REGISTERS, TYPE_BIT, TYPE_INT

    meta = REGISTERS[name]
    if meta[KEY_DATATYPE] == TYPE_BIT:
        return bool(raw)
    if meta[KEY_DATATYPE] == TYPE_INT and raw >= 0x8000:
        raw -= 0x10000
    return raw / meta[KEY_SCALE] if meta[KEY_SCALE] != 1 else raw


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
