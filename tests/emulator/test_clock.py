"""Emulator simulated clock plays a climate profile back (requirement-22)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from genesis_wp.emulator import Emulator, get_profile
from genesis_wp.emulator.climate import BUCKETS, LICENSE, SOURCE, ClimateProfile

from ..harness import PROFILE_615

START = datetime(2026, 1, 15, 0, 0)


def month_profile() -> ClimateProfile:
    """Distinct value per month and hour: month m, hour h -> m + h / 100 (°C)."""
    values = tuple(tuple(round(m + h / 100, 2) for h in range(24)) for m in range(1, BUCKETS["month"] + 1))
    return ClimateProfile("month", date(2021, 1, 1), date(2025, 12, 31), 0.0, 0.0, "UTC", SOURCE, LICENSE, values)


def emulator(**options) -> Emulator:
    options = {"climate": month_profile(), "start_time": START, **options}
    return Emulator(get_profile(PROFILE_615), port=0, dynamics=True, **options)


def outdoor(emu: Emulator) -> float:
    raw = emu.heating.inputs()[13]
    return (raw - 0x10000 if raw >= 0x8000 else raw) / 100


# DHF-REQ: genesis-wp/requirement-22  DHF-TEST: genesis-wp/ac-75
def test_outdoor_is_the_profile_value_of_the_simulated_month_and_hour():
    emu = emulator()
    assert outdoor(emu) == 1.00  # January, hour 0
    emu.advance(3 * 3600)
    assert emu.simulated_time == datetime(2026, 1, 15, 3, 0)
    assert outdoor(emu) == 1.03
    emu.advance(59 * 60)
    assert emu.simulated_time == datetime(2026, 1, 15, 3, 59)
    assert outdoor(emu) == 1.03  # held for the whole hour, no interpolation
    emu.advance(60)
    assert outdoor(emu) == 1.04


# DHF-REQ: genesis-wp/requirement-22  DHF-TEST: genesis-wp/ac-76
def test_simulated_time_follows_playback_speed_changed_while_running():
    emu = emulator(playback_speed=60)
    emu.play(10)
    assert emu.simulated_time - START == timedelta(seconds=600)
    emu.set_playback_speed(120)
    emu.play(10)
    assert emu.simulated_time - START == timedelta(seconds=1800)


# DHF-REQ: genesis-wp/requirement-22  DHF-TEST: genesis-wp/ac-77
def test_plant_response_over_an_hour_does_not_depend_on_speed():
    slow, fast = emulator(playback_speed=60, seed=7), emulator(playback_speed=3600, seed=7)
    slow.play(60)
    fast.play(1)
    assert slow.simulated_time == fast.simulated_time == START + timedelta(hours=1)
    for attribute in ("t33", "t31", "t32"):
        assert getattr(slow.heating, attribute) == pytest.approx(getattr(fast.heating, attribute), abs=0.01)


# DHF-REQ: genesis-wp/requirement-22  DHF-TEST: genesis-wp/ac-78
def test_held_outdoor_temperature_overrides_the_profile():
    emu = emulator(outdoor_temperature=-5.0)
    for _ in range(24):
        emu.advance(3600)
        assert outdoor(emu) == -5.0


def test_partial_minutes_carry_over():
    emu = emulator(playback_speed=1)
    emu.play(30)
    assert emu.simulated_time == START
    emu.play(30)
    assert emu.simulated_time == START + timedelta(minutes=1)


def test_speed_must_be_positive():
    with pytest.raises(ValueError):
        emulator(playback_speed=0)
    emu = emulator()
    with pytest.raises(ValueError):
        emu.set_playback_speed(-1)


def test_climate_needs_dynamics():
    with pytest.raises(ValueError, match="dynamics"):
        Emulator(get_profile(PROFILE_615), port=0, climate=month_profile())


def test_without_a_climate_profile_there_is_no_clock():
    emu = Emulator(get_profile(PROFILE_615), port=0, dynamics=True)
    assert emu.simulated_time is None
    with pytest.raises(RuntimeError):
        emu.advance(60)


async def test_ticker_plays_at_the_configured_speed(start_emulator):
    emu = await start_emulator(PROFILE_615, dynamics=True, dynamics_interval=0.05,
                               climate=month_profile(), start_time=START, playback_speed=1200)
    import asyncio

    await asyncio.sleep(0.3)
    assert emu.simulated_time >= START + timedelta(minutes=1)
