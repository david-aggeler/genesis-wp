"""Opt-in compressor cycling in the emulator (requirement-46, issue-14).

The emulator steps one simulated minute at a time; the library reads after each step.
"""

from __future__ import annotations

import pytest

from genesis_wp.emulator import compressor
from genesis_wp.emulator.server import Emulator
from genesis_wp.emulator.profile import get_profile

from ..harness import PROFILE_615, emulator_client

pytestmark = pytest.mark.dynamics

INTEGRAL = "input_calculated_heating_demand"
RPM = "input_compressor_speed_rpm"
PERCENT = "input_compressor_speed_percent"
PUMP = "input_condenser_circulation_pump_speed"
BLOCK = "input_compressor_temporarily_blocked"
DEMAND = "input_first_prioritised_demand"
STATUS = "input_status_bits"
SIGNAL = "dinput_compressor_control_signal"
OK = "dinput_heat_pump_ok_to_start"
NAMES = [INTEGRAL, RPM, PERCENT, PUMP, BLOCK, DEMAND, STATUS, SIGNAL, OK]
GEAR_SPEEDS = {*compressor.GEARS, compressor.START_RPM, compressor.BOOST_RPM}


async def cycling(start_emulator, **options):
    return await start_emulator(PROFILE_615, dynamics=True, outdoor_temperature=11.0, compressor_cycling=True, **options)


async def readings(emu, steps, names=NAMES):
    client = emulator_client(emu.host, port=emu.port, kind=emu.profile.model, delay=0)
    try:
        rows = []
        for _ in range(steps):
            emu.step()
            rows.append(await client.async_update(only_registers=names))
        return rows
    finally:
        await client.close()


def transitions(rows):
    """Indices where the compressor signal changes: (index, 'start' | 'stop')."""
    return [(i, "start" if rows[i][SIGNAL] else "stop")
            for i in range(1, len(rows)) if rows[i][SIGNAL] != rows[i - 1][SIGNAL]]


def demand_raw(value):
    """Input 1 as raw: the library may decode it to a label."""
    labels = {"Heat": compressor.DEMAND_HEAT, "No demand": compressor.DEMAND_NONE}
    return labels.get(value, value)


def test_cycling_needs_dynamics():
    with pytest.raises(ValueError, match="dynamics"):
        Emulator(get_profile(PROFILE_615), port=0, compressor_cycling=True)


def test_pump_table():
    assert compressor.pump_percent(0) == 0.0
    assert compressor.pump_percent(1400) == compressor.pump_percent(1600) == 35.0
    assert compressor.pump_percent(2004) == pytest.approx(41.5)
    assert compressor.pump_percent(2408) == compressor.pump_percent(3200) == 48.0


# DHF-REQ: genesis-wp/requirement-46  DHF-TEST: genesis-wp/ac-184
async def test_compressor_stops_and_starts_on_the_integral_thresholds(start_emulator):
    emu = await cycling(start_emulator)
    rows = await readings(emu, 1440)
    events = transitions(rows)
    assert sum(kind == "stop" for _, kind in events) >= 2
    assert sum(kind == "start" for _, kind in events) >= 2
    for i, kind in events:
        if kind == "stop":
            assert rows[i - 1][INTEGRAL] >= compressor.STOP_AT
        else:
            assert rows[i - 1][INTEGRAL] <= compressor.START_AT
    for row in rows:
        if row[SIGNAL]:
            assert row[INTEGRAL] <= compressor.STOP_AT + 0.03 + 1e-9  # reads are rounded to 0.01
        else:
            assert (row[RPM], row[PERCENT], row[PUMP]) == (0, 0.0, 0.0)
            assert row[INTEGRAL] >= compressor.START_AT - 0.09 - 1e-9


# DHF-REQ: genesis-wp/requirement-46  DHF-TEST: genesis-wp/ac-185
async def test_stop_and_start_signatures(start_emulator):
    emu = await cycling(start_emulator)
    rows = await readings(emu, 700)
    events = transitions(rows)
    stop = next(i for i, kind in events if kind == "stop")
    start = next(i for i, kind in events if kind == "start" and i > stop)
    blocks = [row[BLOCK] for row in rows[stop:start]]
    assert blocks[:7] == [300, 240, 180, 120, 60, 0, 0]
    assert set(blocks[5:]) == {0}
    assert all(row[OK] == (row[BLOCK] == 0) for row in rows[stop:start])
    for row in rows:
        expected = (compressor.DEMAND_HEAT, 8) if row[SIGNAL] else (compressor.DEMAND_NONE, 1024)
        assert (demand_raw(row[DEMAND]), row[STATUS]) == expected
    run = rows[start:start + 21]
    assert [row[OK] for row in run] == [False] * 20 + [True]


# DHF-REQ: genesis-wp/requirement-46  DHF-TEST: genesis-wp/ac-186
async def test_speed_follows_start_sequence_gears_and_pump_table(start_emulator):
    emu = await cycling(start_emulator)
    rows = await readings(emu, 700)
    start = next(i for i, kind in transitions(rows) if kind == "start")
    assert [rows[start + k][RPM] for k in range(3)] == [2408, 2408, 1400]
    running = [row for row in rows if row[SIGNAL]]
    assert all(row[RPM] in GEAR_SPEEDS for row in running)
    for row in running:
        assert row[PERCENT] == pytest.approx(row[RPM] / 72, abs=0.01)
        assert row[PUMP] == pytest.approx(compressor.pump_percent(row[RPM]), abs=0.01)
    _assert_gear_steps(rows[start + 2:])


def _assert_gear_steps(rows):
    """Within one run: one-gear steps on input 30 beyond +-1.0, at least 5 reads apart."""
    last_change = None
    for i in range(1, len(rows)):
        before, after = rows[i - 1], rows[i]
        if not (before[SIGNAL] and after[SIGNAL]):
            break
        if before[RPM] == after[RPM] or compressor.BOOST_RPM in (before[RPM], after[RPM]):
            continue
        up = compressor.GEARS.index(after[RPM]) - compressor.GEARS.index(before[RPM])
        assert abs(up) == 1
        assert before[INTEGRAL] <= -1.0 if up > 0 else before[INTEGRAL] >= 1.0
        assert last_change is None or i - last_change >= 5
        last_change = i


# DHF-REQ: genesis-wp/requirement-46  DHF-TEST: genesis-wp/ac-186
async def test_boost_after_122_minutes_below_2400(start_emulator):
    emu = await cycling(start_emulator)
    # A load that matches capacity holds input 30 inside the gear band, so the run stays at its gear.
    emu.compressor.run_rate = 0.0
    emu.compressor.integral = 0.0
    rows = await readings(emu, 260, names=[RPM, SIGNAL])
    speeds = [row[RPM] for row in rows]
    assert all(row[SIGNAL] for row in rows)
    boosts = [i for i in range(1, len(speeds)) if speeds[i] == 2408 and speeds[i - 1] != 2408]
    assert len(boosts) == 2
    assert all(speeds[i] == speeds[i + 1] == 2408 and speeds[i + 2] < 2400 for i in boosts)
    assert boosts[1] - boosts[0] == 122 + 2


# DHF-REQ: genesis-wp/requirement-46  DHF-TEST: genesis-wp/ac-187
async def test_cycling_off_keeps_the_drift(start_emulator):
    emu = await start_emulator(PROFILE_615, dynamics=True, outdoor_temperature=11.0)
    rows = await readings(emu, 300, names=[RPM, SIGNAL])
    assert all(row[SIGNAL] for row in rows)
    assert all(1498 <= row[RPM] <= 2098 for row in rows)


# DHF-REQ: genesis-wp/requirement-46  DHF-TEST: genesis-wp/ac-187
async def test_cycle_reproduces_the_median_run_and_stop(start_emulator):
    emu = await cycling(start_emulator)
    rows = await readings(emu, 900, names=[SIGNAL])
    events = transitions(rows)
    stop = next(i for i, kind in events if kind == "stop")
    start1 = next(i for i, kind in events if kind == "start" and i > stop)
    stop2 = next(i for i, kind in events if kind == "stop" and i > start1)
    assert start1 - stop == pytest.approx(127, abs=3)
    assert stop2 - start1 == pytest.approx(189, abs=3)


# DHF-REQ: genesis-wp/requirement-46  DHF-TEST: genesis-wp/ac-187
async def test_compressor_stays_stopped_in_passive_cooling(start_emulator):
    emu = await cycling(start_emulator, overrides={("coils", 33): 1, ("holding_registers", 298): 1})
    rows = await readings(emu, 300, names=[RPM, SIGNAL])
    assert all(not row[SIGNAL] and row[RPM] == 0 for row in rows)


async def test_hc2_supply_stays_below_system_supply_as_it_cools(start_emulator):
    emu = await start_emulator(
        PROFILE_615, dynamics=True, outdoor_temperature=10.0, compressor_cycling=True
    )
    supply = "input_system_supply_line_temperature"
    hc2 = "input_mix_valve_1_supply_line_temperature"
    rows = await readings(emu, 160, names=[SIGNAL, supply, hc2])
    stop = next(i for i, kind in transitions(rows) if kind == "stop")
    assert rows[stop + 30][supply] < rows[stop][supply]
    for minute, row in enumerate(rows[stop:], start=1):
        assert row[hc2] <= row[supply], minute
