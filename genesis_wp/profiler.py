"""Read-only JSON Lines recordings. No target identity is written to disk.

``clock`` may supply ``now()`` (an aware UTC datetime), ``monotonic()`` (seconds),
and async ``sleep(seconds)``. A canceled run leaves a readable, truncated prefix.
An unavailable first read yields a header with null firmware; later samples still run.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from . import EnumValue, ThermiaException, ThermiaGenesis


class Clock:
    """Wall time for provenance, monotonic time for scheduling."""

    now = staticmethod(lambda: datetime.now(timezone.utc))
    monotonic = staticmethod(time.monotonic)
    sleep = staticmethod(asyncio.sleep)


def _positive_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a finite positive number')
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be a finite positive number')


def _sample_count(interval, duration, samples):
    _positive_number(interval, 'interval')
    if (duration is None) == (samples is None):
        raise ValueError('pass exactly one of duration and samples')
    if duration is not None:
        _positive_number(duration, 'duration')
        return math.floor(duration / interval)
    if type(samples) is not int or samples <= 0:
        raise ValueError('samples must be a positive integer')
    return samples


def _json_value(value):
    if isinstance(value, EnumValue):
        return {'raw': value.raw, 'label': value.label}
    raise TypeError('unsupported decoded value')


async def _append(stream, entry):
    text = json.dumps(entry, default=_json_value, allow_nan=False) + '\n'
    await asyncio.to_thread(_write_line, stream, text)


def _write_line(stream, text):
    stream.write(text)
    stream.flush()


async def _read_sample(client, ts):
    try:
        decoded = await client.async_update()
    except (ThermiaException, OSError) as error:
        return {'type': 'failure', 'ts': ts, 'error': type(error).__name__}
    return {'type': 'sample', 'ts': ts, 'raw': client.last_words, 'decoded': decoded}


# DHF-REQ: genesis-wp/requirement-40
# DHF-REQ: genesis-wp/requirement-41
async def profile(host=None, *, port=502, unit=None, model, interval, output,
                  duration=None, samples=None, clock=None):
    """Append one run, reading every model register on a fixed interval grid.

    Exactly one of positive ``duration`` (seconds) and integer ``samples`` is
    required. Duration produces floor(duration / interval) attempts. An injected
    Modbus unit remains owned by its caller, including transport-host redaction.
    """
    count = _sample_count(interval, duration, samples)
    client = ThermiaGenesis(host, port=port, unit=unit, kind=model, delay=0, read_only=True)
    clock = clock if clock is not None else Clock()
    try:
        stream = await asyncio.to_thread(Path(output).open, 'a', encoding='utf-8')
        try:
            await _record(client, stream, model, interval, duration, count, clock)
        finally:
            await asyncio.to_thread(stream.close)
    finally:
        await client.close()


async def _record(client, stream, model, interval, duration, count, clock):
    started_at = clock.now().isoformat()
    start = clock.monotonic()
    header = dict(type='header', format_version=1, model=model,
                  library_version=version('genesis-wp'), interval_s=interval,
                  requested_samples=count, started_at=started_at)
    if duration is not None:
        header['requested_duration_s'] = duration
    successes = failures = 0
    for index in range(count):
        await clock.sleep(max(0, start + index * interval - clock.monotonic()))
        entry = await _read_sample(client, clock.now().isoformat())
        if index == 0:
            await _append(stream, dict(header, firmware=client.firmware))
        await _append(stream, entry)
        successes += entry['type'] == 'sample'
        failures += entry['type'] == 'failure'
    if count == 0:
        await _append(stream, dict(header, firmware=None))
    await _append(stream, dict(type='trailer', ended_at=clock.now().isoformat(),
                               samples=successes, failures=failures))
