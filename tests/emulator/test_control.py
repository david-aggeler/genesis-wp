"""Emulator control channel and its client (requirement-24)."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
from datetime import date, datetime, timedelta

import pytest

from genesis_wp.emulator.climate import BUCKETS, LICENSE, SOURCE, ClimateProfile
from genesis_wp.emulator.control import EMULATOR_NAME, PROTOCOL, ControlError, EmulatorControl, NotAnEmulator

from ..harness import PROFILE_615, library_full_update

pytestmark = pytest.mark.emulator_control

START = datetime(2026, 1, 15, 0, 0)


def month_profile() -> ClimateProfile:
    """Distinct value per month and hour: month m, hour h -> m + h / 100 (°C)."""
    values = tuple(tuple(round(m + h / 100, 2) for h in range(24)) for m in range(1, BUCKETS["month"] + 1))
    return ClimateProfile("month", date(2021, 1, 1), date(2025, 12, 31), 0.0, 0.0, "UTC", SOURCE, LICENSE, values)


async def clocked(start_emulator, **options):
    options = {"dynamics": True, "climate": month_profile(), "start_time": START, "control_port": 0, **options}
    emu = await start_emulator(PROFILE_615, **options)
    return emu, EmulatorControl(emu.host, emu.control_port)


async def outdoor_over_modbus(emu) -> float:
    return (await library_full_update(emu))["input_outdoor_temperature"]


async def raw_request(emu, line: bytes) -> dict:
    reader, writer = await asyncio.open_connection(emu.host, emu.control_port)
    writer.write(line)
    await writer.drain()
    reply = json.loads(await asyncio.wait_for(reader.readline(), 2))
    writer.close()
    await writer.wait_closed()
    return reply


# DHF-REQ: genesis-wp/requirement-24  DHF-TEST: genesis-wp/ac-84
async def test_identify_names_emulator_profile_protocol_and_version(start_emulator):
    emu, control = await clocked(start_emulator)
    assert emu.control_port > 0  # control_port=0 binds a free port and reports it
    async with control:
        identity = await control.identify()
    assert identity.emulator == EMULATOR_NAME == "genesis-wp-emulator"
    assert identity.profile == PROFILE_615
    assert identity.protocol == PROTOCOL == 1
    assert identity.version == importlib.metadata.version("genesis-wp")
    assert identity.firmware["software"] == "6.1.5"


# DHF-REQ: genesis-wp/requirement-24  DHF-TEST: genesis-wp/ac-85
async def test_identify_against_a_modbus_port_is_not_an_emulator(start_emulator):
    emu = await start_emulator(PROFILE_615)
    before = asyncio.get_running_loop().time()
    with pytest.raises(NotAnEmulator):
        async with EmulatorControl(emu.host, emu.port, timeout=2.0) as control:
            await control.identify()
    assert asyncio.get_running_loop().time() - before < 2.5
    assert emu.requests == []


async def test_identify_against_a_closed_port_is_not_an_emulator(start_emulator):
    from genesis_wp.emulator.server import _free_port

    with pytest.raises(NotAnEmulator) as caught:
        async with EmulatorControl("127.0.0.1", _free_port("127.0.0.1"), timeout=1.0) as control:
            await control.identify()
    assert "127.0.0.1" not in str(caught.value)


# DHF-REQ: genesis-wp/requirement-24  DHF-TEST: genesis-wp/ac-86
async def test_playback_speed_set_over_the_channel_takes_effect(start_emulator):
    emu, control = await clocked(start_emulator, playback_speed=60)
    async with control:
        await control.set_playback_speed(120)
        state = await control.state()
    assert state.playback_speed == 120
    assert emu.playback_speed == 120
    emu.play(10)
    assert emu.simulated_time == START + timedelta(seconds=1200)


# DHF-REQ: genesis-wp/requirement-24  DHF-TEST: genesis-wp/ac-87
@pytest.mark.parametrize("speed", [0, -1, float("nan"), float("inf"), "60", True, None])
async def test_invalid_playback_speed_is_rejected_and_changes_nothing(start_emulator, speed):
    emu, control = await clocked(start_emulator, playback_speed=60)
    async with control:
        with pytest.raises(ControlError) as caught:
            await control.set_playback_speed(speed)
        assert caught.value.code == "invalid_value"
        assert (await control.state()).playback_speed == 60


async def test_nan_on_the_wire_is_rejected(start_emulator):
    emu, _ = await clocked(start_emulator, playback_speed=60)
    reply = await raw_request(emu, b'{"op": "set_playback_speed", "args": {"speed": NaN}}\n')
    assert reply["ok"] is False and reply["error"]["code"] == "invalid_value"
    assert emu.playback_speed == 60


@pytest.mark.parametrize("line, code", [
    (b"not json\n", "bad_request"),
    (b"[1, 2]\n", "bad_request"),
    (b'{"args": {}}\n', "bad_request"),
    (b'{"op": "state", "args": []}\n', "bad_request"),
    (b'{"op": "set_playback_speed", "args": {"speed": 2, "extra": 1}}\n', "invalid_value"),
    (b'{"op": "set_playback_speed", "args": {}}\n', "invalid_value"),
])
async def test_malformed_requests_get_a_structured_error(start_emulator, line, code):
    emu, _ = await clocked(start_emulator)
    reply = await raw_request(emu, line)
    assert reply == {"ok": False, "error": {"code": code, "message": reply["error"]["message"]}}


# DHF-REQ: genesis-wp/requirement-24  DHF-TEST: genesis-wp/ac-88
async def test_paused_emulator_keeps_its_simulated_time(start_emulator):
    emu, control = await clocked(start_emulator, dynamics_interval=0.05, playback_speed=1200)
    async with control:
        await control.pause()
        paused = await control.state()
        await asyncio.sleep(0.25)  # five ticker intervals
        still = await control.state()
        assert paused.paused and still.paused
        assert still.simulated_time == paused.simulated_time
        await control.resume()
        await asyncio.sleep(0.2)
        resumed = await control.state()
    assert not resumed.paused
    assert resumed.simulated_time > paused.simulated_time


# DHF-REQ: genesis-wp/requirement-24  DHF-TEST: genesis-wp/ac-89
async def test_jumping_simulated_time_serves_that_hours_climate_value(start_emulator):
    emu, control = await clocked(start_emulator)
    async with control:
        await control.set_simulated_time(datetime(2026, 7, 15, 14, 0))
        state = await control.state()
    assert state.simulated_time == datetime(2026, 7, 15, 14, 0)
    assert await outdoor_over_modbus(emu) == 7.14  # July, hour 14


@pytest.mark.parametrize("value", ["2026-07-15T14:00+02:00", "yesterday", 1720000000])
async def test_simulated_time_must_be_a_naive_iso_datetime(start_emulator, value):
    emu, _ = await clocked(start_emulator)
    reply = await raw_request(emu, json.dumps({"op": "set_simulated_time", "args": {"time": value}}).encode() + b"\n")
    assert reply["error"]["code"] == "invalid_value"
    assert emu.simulated_time == START


# DHF-REQ: genesis-wp/requirement-24  DHF-TEST: genesis-wp/ac-90
async def test_outdoor_temperature_can_be_held_and_released(start_emulator):
    emu, control = await clocked(start_emulator)
    async with control:
        await control.hold_outdoor(-5.0)
        for _ in range(24):
            emu.advance(3600)
            assert await outdoor_over_modbus(emu) == -5.0
        assert (await control.state()).outdoor_held
        await control.release_outdoor()
        state = await control.state()
    assert not state.outdoor_held
    assert emu.simulated_time == datetime(2026, 1, 16, 0, 0)
    assert await outdoor_over_modbus(emu) == 1.00  # January, hour 0


@pytest.mark.parametrize("celsius", [-51, 61, float("nan")])
async def test_held_outdoor_temperature_must_be_plausible(start_emulator, celsius):
    emu, control = await clocked(start_emulator)
    async with control:
        with pytest.raises(ControlError) as caught:
            await control.hold_outdoor(celsius)
    assert caught.value.code == "invalid_value"
    assert not emu.heating.hold_outdoor


async def test_clock_operations_without_a_clock_are_unavailable(start_emulator):
    emu = await start_emulator(PROFILE_615, control_port=0)
    async with EmulatorControl(emu.host, emu.control_port) as control:
        state = await control.state()
        assert state.simulated_time is None and state.playback_speed is None and not state.dynamics
        for call in (control.set_playback_speed(2), control.pause(), control.set_simulated_time(START),
                     control.hold_outdoor(0.0)):
            with pytest.raises(ControlError) as caught:
                await call
            assert caught.value.code == "unavailable"


# DHF-REQ: genesis-wp/requirement-24  DHF-TEST: genesis-wp/ac-91
async def test_no_hc2_room_set_point_operation(start_emulator):
    emu, control = await clocked(start_emulator)
    before = (emu.room_set_point_hc2, emu.heating.holding_registers())
    reply = await raw_request(emu, b'{"op": "set_room_set_point_hc2", "args": {"celsius": 23.0}}\n')
    assert reply["error"]["code"] == "unknown_operation"
    assert (emu.room_set_point_hc2, emu.heating.holding_registers()) == before
    assert not any("room_set_point" in name for name in dir(EmulatorControl))


# DHF-REQ: genesis-wp/requirement-24  DHF-TEST: genesis-wp/ac-93
async def test_control_channel_leaves_the_modbus_surface_unchanged(start_emulator):
    plain = await start_emulator(PROFILE_615, dynamics=True, seed=3)
    controlled = await start_emulator(PROFILE_615, dynamics=True, seed=3, control_port=0)
    assert await library_full_update(plain) == await library_full_update(controlled)
    assert plain.control_port is None


async def test_control_port_closes_with_the_emulator(start_emulator):
    emu, _ = await clocked(start_emulator)
    port = emu.control_port
    await emu.stop()
    with pytest.raises(OSError):
        await asyncio.open_connection(emu.host, port)
