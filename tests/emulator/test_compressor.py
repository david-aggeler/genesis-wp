"""Opt-in compressor cycling in the emulator (requirement-46, issue-14, issue-39).

The emulator steps one simulated minute at a time; the library reads after each step. Every
write here goes to the emulator only, never to the real plant.
"""

from __future__ import annotations

import pytest

from genesis_wp.emulator import compressor
from genesis_wp.emulator.server import Emulator
from genesis_wp.emulator.profile import get_profile

from ..harness import PROFILE_615, UNIT, emulator_client

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
SUPPLY = "input_system_supply_line_temperature"
SET_POINT = "input_system_supply_line_calculated_set_point"
GEAR_SPEEDS = {*compressor.GEARS, compressor.START_RPM, compressor.BOOST_RPM}
MILD, COLD = 12.0, 5.0  # [°C] the plant cycles at 10-12 °C and modulates below about 9.7 °C


async def cycling(start_emulator, outdoor=MILD, **options):
    return await start_emulator(PROFILE_615, dynamics=True, outdoor_temperature=outdoor, compressor_cycling=True, **options)


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
            assert row[INTEGRAL] <= compressor.STOP_AT + 0.1 + 1e-9  # reads are rounded to 0.01
        else:
            assert (row[RPM], row[PERCENT], row[PUMP]) == (0, 0.0, 0.0)
            assert row[INTEGRAL] >= compressor.START_AT - 0.1 - 1e-9


# DHF-REQ: genesis-wp/requirement-46  DHF-TEST: genesis-wp/ac-185
async def test_stop_and_start_signatures(start_emulator):
    emu = await cycling(start_emulator)
    rows = await readings(emu, 900)
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
    rows = await readings(emu, 900)
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
def test_boost_after_122_minutes_below_2400():
    # T33 held at input 18 (error 0) keeps input 30 inside the gear band, so the run stays at its gear.
    model = compressor.CompressorModel(True, 0.0, minutes=compressor.OK_DELAY_MINUTES + 1)
    speeds = []
    for _ in range(260):
        model.step(0.0)
        assert model.running
        speeds.append(model.rpm)
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
async def test_compressor_modulates_without_stopping_in_cold_weather(start_emulator):
    emu = await cycling(start_emulator, outdoor=COLD)
    rows = await readings(emu, 1440, names=[SIGNAL])
    first_start = next((i for i, row in enumerate(rows) if row[SIGNAL]), None)
    assert first_start is not None
    assert all(row[SIGNAL] for row in rows[first_start:])


# DHF-REQ: genesis-wp/requirement-46  DHF-TEST: genesis-wp/ac-187
async def test_compressor_cycles_in_mild_weather(start_emulator):
    emu = await cycling(start_emulator)
    rows = await readings(emu, 1440, names=[SIGNAL])
    events = [kind for _, kind in transitions(rows)]
    first_start = events.index("start")
    complete = events[first_start:].count("start") - 1  # start, run, stop, start
    assert complete >= 2


# DHF-REQ: genesis-wp/requirement-46  DHF-TEST: genesis-wp/ac-187
async def test_compressor_stays_stopped_in_passive_cooling(start_emulator):
    emu = await cycling(start_emulator, overrides={("coils", 33): 1, ("holding_registers", 298): 1})
    rows = await readings(emu, 300, names=[RPM, SIGNAL])
    assert all(not row[SIGNAL] and row[RPM] == 0 for row in rows)


async def test_hc2_supply_stays_below_system_supply_as_it_cools(start_emulator):
    emu = await cycling(start_emulator)
    supply = SUPPLY
    hc2 = "input_mix_valve_1_supply_line_temperature"
    rows = await readings(emu, 600, names=[SIGNAL, supply, hc2])
    stop = next(i for i, kind in transitions(rows) if kind == "stop")
    assert rows[stop + 30][supply] < rows[stop][supply]
    for minute, row in enumerate(rows[stop:], start=1):
        assert row[hc2] <= row[supply], minute


def running_above_lowest_gear():
    model = compressor.CompressorModel(True, 0.0, minutes=compressor.OK_DELAY_MINUTES + 1, gear=3)
    model.since_gear_change = 0
    return model


# DHF-REQ: genesis-wp/requirement-46  DHF-TEST: genesis-wp/ac-208
@pytest.mark.parametrize(("running", "error", "gain"), [(True, 1.0, compressor.RUN_GAIN), (False, -2.0, compressor.STOP_GAIN)])
def test_input_30_moves_one_eighth_toward_the_regime_target(running, error, gain):
    model = running_above_lowest_gear() if running else compressor.CompressorModel(False, 0.0, minutes=10)
    target = gain * error
    for minute in range(60):
        before = model.integral
        model.step(error)
        assert model.running is running, minute
        assert model.integral - before == pytest.approx((target - before) / 8, abs=0.01), minute
    assert model.integral == pytest.approx(target, abs=0.05)


# DHF-REQ: genesis-wp/requirement-46  DHF-TEST: genesis-wp/ac-209
def test_lowest_gear_integrates_the_error_and_a_start_clears_it():
    model = compressor.CompressorModel(True, 0.0, minutes=compressor.OK_DELAY_MINUTES + 1)
    values = []
    for _ in range(90):
        model.step(1.0)
        assert model.rpm == compressor.GEARS[0]
        values.append(model.integral)
    assert values[89] - values[29] == pytest.approx(60 * compressor.LOW_GEAR_RATE, abs=0.10)
    model._stop()
    model.integral = compressor.START_AT - 0.1
    model.step(1.0)
    assert model.running and model.accumulated == 0.0
    model.minutes = compressor.START_MINUTES + 1
    model.gear, model.since_gear_change = 3, 0
    for _ in range(60):
        model.step(1.0)
    assert model.integral == pytest.approx(compressor.RUN_GAIN * 1.0, abs=0.05)


# DHF-REQ: genesis-wp/requirement-46  DHF-TEST: genesis-wp/ac-210
async def test_hc1_room_set_point_step_ramps_up_then_down_without_a_stop(start_emulator, connect):
    emu = await cycling(start_emulator, outdoor=4.8)
    modbus = await connect(emu)  # raw writer, the emulator only
    client = emulator_client(emu.host, port=emu.port, kind=emu.profile.model, delay=0)
    names = [SIGNAL, RPM, SUPPLY, SET_POINT]
    try:
        assert not (await modbus.write_register(5, 2100, device_id=UNIT)).isError()
        settled = 0
        for _ in range(1440):
            emu.step()
            row = await client.async_update(only_registers=names)
            settled = settled + 1 if row[SIGNAL] else 0
            if settled >= 60:
                break
        assert settled >= 60, "no 60 running minutes without a transition"
        before = row[SET_POINT]
        assert not (await modbus.write_register(5, 2200, device_id=UNIT)).isError()
        rows = []
        for _ in range(180):
            emu.step()
            rows.append(await client.async_update(only_registers=names))
    finally:
        await client.close()
    assert all(row[SET_POINT] - before == pytest.approx(2.0, abs=0.05) for row in rows)
    assert any(row[RPM] >= 4150 for row in rows[:30])
    cross = next(i for i, row in enumerate(rows) if row[SUPPLY] >= row[SET_POINT])
    assert 30 <= cross <= 90
    assert all(row[SIGNAL] for row in rows)
    assert any(rows[i][RPM] < rows[i - 1][RPM] for i in range(cross + 1, len(rows)))
