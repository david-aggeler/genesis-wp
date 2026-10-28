"""Climate profiles: the typical outdoor temperature of a location (requirement-21).

A climate profile holds one typical day per bucket: for each month (12) or week (52) of the
year, the mean outdoor temperature at every local hour of the day over a period of years.
It is built from the hourly ``temperature_2m`` of the Open-Meteo historical weather archive
(ERA5 reanalysis, CC BY 4.0) without interpolation, and saved as JSON (format
``genesis-wp-climate/1``). The emulator plays it back (requirement-22).

Only :func:`build` touches the network; loading and playing a profile never does. The
location is the owner's home: it is stored in the profile file but never logged.

Command line::

    genesis-wp-climate build --latitude 47.37 --longitude 8.54 -o climate.json
    genesis-wp-climate build --latitude 47.37 --longitude 8.54 --bucket week --years 3 -o climate.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

FORMAT = "genesis-wp-climate/1"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
SOURCE = "Open-Meteo historical weather API (ERA5 reanalysis), https://open-meteo.com"
LICENSE = "CC BY 4.0"
#: Buckets and how many there are per year.
BUCKETS = {"month": 12, "week": 52}
HOURS = 24

#: ``fetch(latitude, longitude, start, end)`` returns the archive's JSON response.
Fetch = Callable[[float, float, date, date], dict]


class ClimateError(ValueError):
    """A climate profile cannot be built or loaded."""


def bucket_index(moment: date | datetime, bucket: str) -> int:
    """0-based bucket of a day: its month, or week min((day of year - 1) // 7, 51)."""
    if bucket == "month":
        return moment.month - 1
    if bucket == "week":
        return min((moment.timetuple().tm_yday - 1) // 7, BUCKETS["week"] - 1)
    raise ClimateError(f"unknown bucket {bucket!r}, expected one of {', '.join(BUCKETS)}")


def last_full_years(years: int = 5, today: date | None = None) -> tuple[date, date]:
    """First and last day of the ``years`` full calendar years before ``today``."""
    last = (today or date.today()).year - 1
    return date(last - years + 1, 1, 1), date(last, 12, 31)


@dataclass(frozen=True)
class ClimateProfile:
    """Mean outdoor temperature [°C] per bucket and local hour of day."""

    bucket: str
    start: date
    end: date
    latitude: float
    longitude: float
    timezone: str
    source: str
    license: str
    #: ``values[bucket][hour]``, 12 or 52 rows of 24 hours.
    values: tuple[tuple[float, ...], ...]

    def value_at(self, moment: datetime) -> float:
        """The profile value for ``moment``'s bucket and hour, held for the whole hour."""
        return self.values[bucket_index(moment, self.bucket)][moment.hour]

    def to_dict(self) -> dict:
        return {
            "format": FORMAT,
            "bucket": self.bucket,
            "period": {"start": self.start.isoformat(), "end": self.end.isoformat()},
            "location": {"latitude": self.latitude, "longitude": self.longitude, "timezone": self.timezone},
            "source": self.source,
            "license": self.license,
            "unit": "°C",
            "values": [list(row) for row in self.values],
        }

    @classmethod
    def from_dict(cls, data: dict) -> ClimateProfile:
        if data.get("format") != FORMAT:
            raise ClimateError(f"unsupported climate format {data.get('format')!r}, expected {FORMAT!r}")
        try:
            profile = cls(
                bucket=data["bucket"],
                start=date.fromisoformat(data["period"]["start"]),
                end=date.fromisoformat(data["period"]["end"]),
                latitude=float(data["location"]["latitude"]),
                longitude=float(data["location"]["longitude"]),
                timezone=str(data["location"]["timezone"]),
                source=str(data["source"]),
                license=str(data["license"]),
                values=tuple(tuple(float(v) for v in row) for row in data["values"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ClimateError(f"malformed climate profile: {exc}") from exc
        if profile.bucket not in BUCKETS:
            raise ClimateError(f"unknown bucket {profile.bucket!r}")
        if len(profile.values) != BUCKETS[profile.bucket] or any(len(row) != HOURS for row in profile.values):
            raise ClimateError(f"a {profile.bucket} profile needs {BUCKETS[profile.bucket]} x {HOURS} values")
        return profile

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1, ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, path: str | Path) -> ClimateProfile:
        return cls.from_dict(json.loads(Path(path).read_text()))


def aggregate(times: Sequence[str], temperatures: Sequence[float | None], bucket: str) -> tuple[tuple[float, ...], ...]:
    """Mean per bucket and hour of the archive's hourly values; missing values are skipped."""
    count = BUCKETS.get(bucket)
    if count is None:
        raise ClimateError(f"unknown bucket {bucket!r}, expected one of {', '.join(BUCKETS)}")
    if len(times) != len(temperatures):
        raise ClimateError("archive response: time and temperature_2m differ in length")
    sums = [[0.0] * HOURS for _ in range(count)]
    counts = [[0] * HOURS for _ in range(count)]
    skipped = 0
    for stamp, value in zip(times, temperatures):
        if value is None:
            skipped += 1
            continue
        moment = datetime.fromisoformat(stamp)
        index = bucket_index(moment, bucket)
        sums[index][moment.hour] += float(value)
        counts[index][moment.hour] += 1
    empty = [(b + 1, h) for b in range(count) for h in range(HOURS) if counts[b][h] == 0]
    if empty:
        raise ClimateError(f"no values for {len(empty)} {bucket}/hour slots, first {bucket} {empty[0][0]} hour {empty[0][1]}")
    _LOGGER.info("aggregated %d hourly values into %d %s x %d hours (%d missing skipped)",
                 len(times) - skipped, count, bucket, HOURS, skipped)
    return tuple(tuple(round(sums[b][h] / counts[b][h], 2) for h in range(HOURS)) for b in range(count))


def fetch_open_meteo(latitude: float, longitude: float, start: date, end: date, timeout: float = 120.0) -> dict:
    """Hourly temperature_2m from the Open-Meteo archive, in local time (``timezone=auto``)."""
    query = urllib.parse.urlencode({
        "latitude": latitude, "longitude": longitude,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "hourly": "temperature_2m", "timezone": "auto",
    })
    # The URL carries the location: never log it.
    with urllib.request.urlopen(f"{ARCHIVE_URL}?{query}", timeout=timeout) as response:  # noqa: S310 (fixed https URL)
        return json.load(response)


def build(latitude: float, longitude: float, start: date | None = None, end: date | None = None,
          bucket: str = "month", fetch: Fetch | None = None) -> ClimateProfile:
    """Fetch the archive for a location and period (default: the last 5 full years) and aggregate it."""
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ClimateError("latitude must be within ±90 and longitude within ±180")
    if bucket not in BUCKETS:
        raise ClimateError(f"unknown bucket {bucket!r}, expected one of {', '.join(BUCKETS)}")
    if start is None or end is None:
        start, end = last_full_years()
    if start > end:
        raise ClimateError(f"period start {start} is after its end {end}")
    _LOGGER.info("fetching hourly temperatures %s .. %s (%d days)", start, end, (end - start).days + 1)
    response = (fetch or fetch_open_meteo)(latitude, longitude, start, end)
    try:
        hourly = response["hourly"]
        values = aggregate(hourly["time"], hourly["temperature_2m"], bucket)
    except (KeyError, TypeError) as exc:
        raise ClimateError(f"unexpected archive response: missing {exc}") from exc
    return ClimateProfile(bucket, start, end, float(latitude), float(longitude),
                          str(response.get("timezone", "")), SOURCE, LICENSE, values)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="genesis-wp-climate",
        description="Build a climate profile for the emulator from the Open-Meteo historical archive.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    b = commands.add_parser("build", help="fetch the archive and write a climate profile JSON")
    b.add_argument("--latitude", type=float, required=True)
    b.add_argument("--longitude", type=float, required=True)
    b.add_argument("--years", type=int, default=5, help="full calendar years before today (default 5)")
    b.add_argument("--start", type=date.fromisoformat, help="first day, YYYY-MM-DD (overrides --years)")
    b.add_argument("--end", type=date.fromisoformat, help="last day, YYYY-MM-DD (overrides --years)")
    b.add_argument("--bucket", choices=sorted(BUCKETS), default="month")
    b.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    start, end = (args.start, args.end) if args.start and args.end else last_full_years(args.years)
    try:
        profile = build(args.latitude, args.longitude, start, end, args.bucket, fetch=fetch_open_meteo)
    except (ClimateError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    profile.save(args.output)
    print(f"wrote {args.output}: {profile.bucket} profile {profile.start} .. {profile.end}, {profile.timezone}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
