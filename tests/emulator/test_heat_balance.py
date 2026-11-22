"""Heat-balance replay from the owner's DEV Home Assistant recorder.

Read-only export by homelab-1, 2026-11-22 15:48–19:41Z, around the HC1 room set
point step 21 → 22 °C at 16:48Z.
"""

import csv
import random
from pathlib import Path
from statistics import mean

import pytest

from genesis_wp.emulator import get_profile
from genesis_wp.emulator.heating import HeatingModel

from ..harness import PROFILE_615

pytestmark = pytest.mark.dynamics


def heating_model(outdoor: float, room_set_point: float | None = None) -> HeatingModel:
    profile = get_profile(PROFILE_615)
    if room_set_point is not None:
        profile = profile.with_overrides({("holding_registers", 5): round(room_set_point * 100)})
    model = HeatingModel.from_values(
        profile.blocks["holding_registers"].value,
        profile.blocks["input_registers"].value,
        outdoor=outdoor,
        rules=profile.room_set_points,
    )
    model.heat_balance = True
    return model


# DHF-TEST: genesis-wp/requirement-20  DHF-TEST: genesis-wp/ac-206
def test_heat_balance_replays_recorded_supply_temperature():
    fixture = Path(__file__).with_name("data") / "hc1-step-20261122.csv"
    with fixture.open(newline="") as capture:
        rows = list(csv.DictReader(capture))
    assert len(rows) == 234
    first = rows[0]
    model = heating_model(float(first["input_13"]), float(first["holding_5"]))
    model.t33 = float(first["input_12"])
    model.t31 = float(first["input_41"])
    room_set_point = float(first["holding_5"])
    rng = random.Random(0)
    errors = []
    for row in rows:
        model.outdoor = float(row["input_13"])
        model.observe({5: int(row["input_5"]), 39: round(float(row["input_39"]) * 100)})
        new_set_point = float(row["holding_5"])
        if new_set_point != room_set_point:
            model.set_room_set_point("hc1", new_set_point)
            room_set_point = new_set_point
        model.step(rng)
        error = model.inputs()[12] / 100 - float(row["input_12"])
        assert abs(error) <= 2.0, row["time_utc"]
        errors.append(error)
    assert mean(error ** 2 for error in errors) ** 0.5 <= 1.0


@pytest.mark.parametrize(("initial_supply", "initial_buffer"), [(31.0, 28.0), (23.0, 19.0)])
# DHF-TEST: genesis-wp/requirement-20  DHF-TEST: genesis-wp/ac-207
def test_heat_balance_holds_supply_after_stop_then_follows_buffer(initial_supply, initial_buffer):
    model = heating_model(10.0)
    # Start with residual heat in the supply line. At 1400 rpm the steady-state
    # supply margin is smaller than the gap required for this stop scenario.
    model.t33, model.t31 = initial_supply, initial_buffer
    rng = random.Random(0)
    model.observe({5: 1400, 39: 3500})
    for _ in range(5):
        model.step(rng)
        at_stop = model.inputs()
        if at_stop[12] - at_stop[41] >= 200:
            break
    else:
        pytest.fail("1400 rpm run did not reach the required 2 K pre-stop gap")
    model.observe({5: 0, 39: 0})
    previous_buffer = at_stop[41]
    for minute in range(1, 121):
        model.step(rng)
        values = model.inputs()
        assert values[41] <= previous_buffer, minute
        if minute <= 30:
            assert abs(values[12] - at_stop[12]) <= 30, minute
        if minute >= 60:
            assert abs(values[12] - values[41]) <= 50, minute
        previous_buffer = values[41]


def test_running_again_restarts_the_supply_hold():
    model = heating_model(10.0)
    rng = random.Random(0)
    model.observe({5: 1400, 39: 3500})
    model.step(rng)

    model.observe({5: 0, 39: 0})
    for _ in range(120):
        model.step(rng)

    model.observe({5: 1400, 39: 3500})
    for _ in range(10):
        model.step(rng)
    at_stop = model.inputs()[12]

    model.observe({5: 0, 39: 0})
    for _ in range(30):
        model.step(rng)
        assert model.inputs()[12] == at_stop

    model.step(rng)
    assert model.inputs()[12] < at_stop
