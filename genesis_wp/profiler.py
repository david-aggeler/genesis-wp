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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from . import (EnumValue, ThermiaException, ThermiaGenesis, decode_register,
               _WORD_BLOCKS, _last_word, apply_valid_when)
from .const import REGISTERS, MODEL_NAMES, KEY_ADDRESS, KEY_REG_TYPE


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


@dataclass
class Recording:
    """One recording run; sample indices count successful samples only."""

    header: dict
    samples: list[dict] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    trailer: dict | None = None

    @property
    def truncated(self):
        return self.trailer is None


def _check_header(header):
    if not isinstance(header, dict):
        raise ValueError('recording header must be an object')
    if header.get('type') != 'header' or type(header.get('format_version')) is not int:
        raise ValueError('recording requires a versioned header')
    if header['format_version'] != 1:
        raise ValueError('unsupported recording format_version')
    if header.get('model') not in MODEL_NAMES:
        raise ValueError('unsupported recording model')


# DHF-REQ: genesis-wp/requirement-42
def load_recording(path):
    """Load one JSON Lines run; a complete prefix without a trailer is truncated.

    Multiple appended runs must be split before loading. Malformed JSON or an
    unexpected entry is rejected rather than silently discarding recorded data.
    """
    with Path(path).open(encoding='utf-8') as stream:
        header_line = stream.readline()
        if not header_line:
            raise ValueError('empty recording')
        header = json.loads(header_line)
        _check_header(header)
        recording = Recording(header)
        for line in stream:
            _load_entry(recording, json.loads(line))
    return recording


def _load_entry(recording, entry):
    if recording.trailer is not None:
        raise ValueError('entry after recording trailer')
    if not isinstance(entry, dict):
        raise ValueError('recording entry must be an object')
    kind = entry.get('type')
    if kind == 'sample':
        _validate_words(entry.get('raw'))
        recording.samples.append(entry)
    elif kind == 'failure':
        recording.failures.append(entry)
    elif kind == 'trailer':
        recording.trailer = entry
    else:
        raise ValueError('unexpected recording entry')


def _validate_words(raw):
    if not isinstance(raw, dict):
        raise ValueError('sample raw words must be an object')
    for block, words in raw.items():
        if block not in _WORD_BLOCKS.values() or not isinstance(words, dict):
            raise ValueError('unknown raw block')
        limit = 1 if block in ('coils', 'discrete_inputs') else 65535
        for address, value in words.items():
            _validate_word(address, value, limit)


def _validate_word(address, value, limit):
    if not str(address).isascii() or not str(address).isdecimal():
        raise ValueError('invalid raw address')
    if str(int(address)) != str(address) or not 0 <= int(address) <= 65535:
        raise ValueError('invalid raw address')
    if type(value) is not int or not 0 <= value <= limit:
        raise ValueError('invalid raw word')


def _model_registers(recording, registers):
    _check_header(recording.header)
    model = recording.header['model']
    if not any(meta.get(model) for meta in registers.values()):
        raise ValueError('recording model differs from register map')
    return {name: meta for name, meta in registers.items() if meta.get(model)}


def _raw_register(sample, meta):
    words = sample['raw'].get(_WORD_BLOCKS[meta[KEY_REG_TYPE]], {})
    address = meta[KEY_ADDRESS]
    width = _last_word(meta) - address + 1
    return [words[str(address + i)] for i in range(width)]


def _decode_sample(sample, model, registers):
    values, raw = {}, {}
    for name, meta in registers.items():
        try:
            words = _raw_register(sample, meta)
        except KeyError:
            continue  # a failed read chunk leaves its registers absent
        raw[name] = words[0]
        values[name] = decode_register(name, words, 0, model, registers)
    apply_valid_when(values, raw, model, registers)
    return values


# DHF-REQ: genesis-wp/requirement-42
def decode_again(recording, registers=None):
    """Return decoded dictionaries in sample order, without any device connection.

    The optional register map uses the library's metadata layout and must support
    the recording's model. Stored decoded values are never used as input.
    """
    registers = _model_registers(recording, REGISTERS if registers is None else registers)
    return [_decode_sample(sample, recording.header['model'], registers)
            for sample in recording.samples]


def _sample_differences(sample, profile, names):
    differences = []
    for block, words in sample['raw'].items():
        for address, word in words.items():
            address = int(address)
            source = profile.blocks[block]
            expected = source.value(address) if source.implemented(address) else None
            if word != expected:
                differences.append((block, address, names.get((block, address)), word, expected))
    return differences


# DHF-REQ: genesis-wp/requirement-42
def compare_with_profile(recording, profile):
    """Return differences by zero-based sample index.

    Each difference is (block, address, register name, recorded word, profile
    word). Unnamed words and addresses absent from the profile use None.
    """
    registers = _model_registers(recording, REGISTERS)
    if profile.model != recording.header['model']:
        raise ValueError('recording and profile models differ')
    names = {(_WORD_BLOCKS[m[KEY_REG_TYPE]], address): name
             for name, m in registers.items()
             for address in range(m[KEY_ADDRESS], _last_word(m) + 1)}
    return {index: _sample_differences(sample, profile, names)
            for index, sample in enumerate(recording.samples)}
