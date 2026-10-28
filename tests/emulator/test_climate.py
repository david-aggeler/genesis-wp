"""Climate profile builder (requirement-21).

The archive response is synthetic but has the Open-Meteo archive's exact shape (local
``timezone=auto`` timestamps, ``hourly.time`` and ``hourly.temperature_2m``): the suite is
loopback-only, so no real response is fetched here.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta

import pytest

from genesis_wp.emulator import climate
from genesis_wp.emulator.climate import ClimateProfile

# A public reference point (Zürich city centre), never the owner's home.
LATITUDE, LONGITUDE = 47.3769, 8.5417


def archive_response(start: date, end: date) -> dict:
    """Hourly temperatures with a seasonal and a daily swing, plus a few gaps."""
    times, values = [], []
    moment = datetime(start.year, start.month, start.day)
    while moment.date() <= end:
        doy = moment.timetuple().tm_yday
        value = 10 - 9 * math.cos(2 * math.pi * (doy - 15) / 365) - 4 * math.cos(2 * math.pi * (moment.hour - 3) / 24)
        times.append(moment.strftime("%Y-%m-%dT%H:%M"))
        values.append(None if (doy, moment.hour) == (100, 12) else round(value + (doy % 7) * 0.1, 1))
        moment += timedelta(hours=1)
    return {
        "latitude": LATITUDE, "longitude": LONGITUDE, "utc_offset_seconds": 3600,
        "timezone": "Europe/Zurich", "timezone_abbreviation": "CET",
        "hourly_units": {"time": "iso8601", "temperature_2m": "°C"},
        "hourly": {"time": times, "temperature_2m": values},
    }


def recorded_fetch(calls: list | None = None):
    def fetch(latitude, longitude, start, end):
        if calls is not None:
            calls.append((start, end))
        return archive_response(start, end)
    return fetch


def expected_mean(response: dict, select) -> float:
    picked = [v for t, v in zip(response["hourly"]["time"], response["hourly"]["temperature_2m"])
              if v is not None and select(datetime.fromisoformat(t))]
    return sum(picked) / len(picked)


PERIOD = (date(2021, 1, 1), date(2023, 12, 31))


# DHF-REQ: genesis-wp/requirement-21  DHF-TEST: genesis-wp/ac-70
def test_month_profile_is_the_mean_per_month_and_hour():
    profile = climate.build(LATITUDE, LONGITUDE, *PERIOD, bucket="month", fetch=recorded_fetch())
    response = archive_response(*PERIOD)
    assert len(profile.values) == 12 and all(len(row) == 24 for row in profile.values)
    for month, hour in ((1, 0), (1, 14), (4, 12), (7, 3), (12, 23)):
        expected = expected_mean(response, lambda t: t.month == month and t.hour == hour)
        assert profile.values[month - 1][hour] == pytest.approx(expected, abs=0.01)


# DHF-REQ: genesis-wp/requirement-21  DHF-TEST: genesis-wp/ac-71
def test_week_profile_has_52_buckets_and_folds_the_last_days_into_week_52():
    profile = climate.build(LATITUDE, LONGITUDE, *PERIOD, bucket="week", fetch=recorded_fetch())
    response = archive_response(*PERIOD)
    assert len(profile.values) == 52 and all(len(row) == 24 for row in profile.values)
    week = lambda t: min((t.timetuple().tm_yday - 1) // 7, 51) + 1  # noqa: E731
    for w, hour in ((1, 0), (26, 12), (52, 6)):
        expected = expected_mean(response, lambda t: week(t) == w and t.hour == hour)
        assert profile.values[w - 1][hour] == pytest.approx(expected, abs=0.01)
    assert climate.bucket_index(datetime(2024, 12, 31, 5), "week") == 51  # day 366
    assert climate.bucket_index(datetime(2023, 12, 31, 5), "week") == 51  # day 365
    assert climate.bucket_index(datetime(2023, 12, 23, 5), "week") == 50  # day 357


# DHF-REQ: genesis-wp/requirement-21  DHF-TEST: genesis-wp/ac-72
def test_profile_json_round_trips(tmp_path):
    profile = climate.build(LATITUDE, LONGITUDE, *PERIOD, bucket="month", fetch=recorded_fetch())
    path = tmp_path / "climate.json"
    profile.save(path)
    loaded = ClimateProfile.load(path)
    assert loaded == profile
    assert (loaded.start, loaded.end, loaded.bucket) == (*PERIOD, "month")
    assert (loaded.latitude, loaded.longitude) == (LATITUDE, LONGITUDE)
    assert "Open-Meteo" in loaded.source and loaded.license == "CC BY 4.0"
    assert loaded.timezone == "Europe/Zurich"


# DHF-REQ: genesis-wp/requirement-21  DHF-TEST: genesis-wp/ac-73
def test_loading_a_profile_needs_no_network(tmp_path):
    """Runs under the conftest network guard: any non-loopback connect or lookup raises."""
    climate.build(LATITUDE, LONGITUDE, *PERIOD, fetch=recorded_fetch()).save(tmp_path / "c.json")
    profile = ClimateProfile.load(tmp_path / "c.json")
    assert profile.value_at(datetime(2026, 1, 15, 3, 30)) == profile.values[0][3]


# DHF-REQ: genesis-wp/requirement-21  DHF-TEST: genesis-wp/ac-74
def test_building_logs_no_coordinates(caplog):
    caplog.set_level(logging.DEBUG, logger="genesis_wp")
    climate.build(LATITUDE, LONGITUDE, *PERIOD, fetch=recorded_fetch())
    assert caplog.records, "the build should log what it did"
    for record in caplog.records:
        assert "47.37" not in record.getMessage() and "8.54" not in record.getMessage()


def test_default_period_is_the_last_five_full_years():
    assert climate.last_full_years(5, today=date(2026, 10, 28)) == (date(2021, 1, 1), date(2025, 12, 31))


def test_a_bucket_hour_without_values_fails_the_build():
    def sparse(latitude, longitude, start, end):
        response = archive_response(start, end)
        hourly = response["hourly"]
        hourly["temperature_2m"] = [None if t.startswith("2023-02") else v
                                    for t, v in zip(hourly["time"], hourly["temperature_2m"])]
        return response
    with pytest.raises(climate.ClimateError, match="no values"):
        climate.build(LATITUDE, LONGITUDE, date(2023, 1, 1), date(2023, 12, 31), fetch=sparse)


@pytest.mark.parametrize(("latitude", "longitude", "bucket"), [(91, 0, "month"), (0, 181, "month"), (0, 0, "day")])
def test_bad_parameters_are_rejected(latitude, longitude, bucket):
    with pytest.raises(climate.ClimateError):
        climate.build(latitude, longitude, *PERIOD, bucket=bucket, fetch=recorded_fetch())


def test_cli_builds_a_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(climate, "fetch_open_meteo", recorded_fetch())
    out = tmp_path / "home.json"
    assert climate.main(["build", "--latitude", "47.3769", "--longitude", "8.5417",
                         "--start", "2023-01-01", "--end", "2023-12-31", "--bucket", "week", "-o", str(out)]) == 0
    assert ClimateProfile.load(out).bucket == "week"
    assert "47.37" not in capsys.readouterr().out
