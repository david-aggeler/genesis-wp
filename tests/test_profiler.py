"""Profile recording contract, against captured emulator profiles."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from genesis_wp import EnumValue
from .harness import PROFILES, PROFILE_615, WPE_I_KIND, library_full_update

pytestmark = pytest.mark.fixture


class Clock:
    def __init__(self):
        self.seconds = 0

    def monotonic(self):
        return self.seconds

    def now(self):
        return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=self.seconds)

    async def sleep(self, seconds):
        self.seconds += seconds


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


# DHF-TEST: genesis-wp/requirement-40 genesis-wp/ac-158 genesis-wp/ac-159
@pytest.mark.parametrize('firmware_profile', PROFILES)
async def test_recording(start_emulator, tmp_path, firmware_profile):
    from genesis_wp.profiler import profile
    emu = await start_emulator(firmware_profile)
    expected = await library_full_update(emu)
    path = tmp_path / 'recording.jsonl'
    await profile(emu.host, port=emu.port, model=WPE_I_KIND, interval=60,
                  samples=3, output=path, clock=Clock())
    header, *samples, trailer = lines(path)
    assert len(samples) == 3
    assert header.items() >= dict(type='header', format_version=1, model=WPE_I_KIND,
        firmware=emu.profile.firmware['software'], interval_s=60, requested_samples=3).items()
    assert header['library_version']
    assert datetime.fromisoformat(header['started_at']).utcoffset() == timedelta(0)
    assert trailer['samples'] == 3 and trailer['failures'] == 0
    assert trailer['type'] == 'trailer' and trailer['ended_at']
    expected = {k: {'raw': v.raw, 'label': v.label} if isinstance(v, EnumValue) else v
                for k, v in expected.items()}
    for sample in samples:
        assert sample['type'] == 'sample'
        assert datetime.fromisoformat(sample['ts']).utcoffset() == timedelta(0)
        assert sample['decoded'] == expected
        assert set(sample['raw']) == set(emu.profile.blocks)
        for block, words in sample['raw'].items():
            for address, word in words.items():
                assert type(word) is int
                assert word == emu.profile.blocks[block].value(int(address))
    if firmware_profile.endswith('6.0.6'):
        assert samples[0]['raw']['input_registers']['30'] == 65508
        assert samples[0]['decoded']['input_calculated_heating_demand'] == -0.28


# DHF-TEST: genesis-wp/requirement-40 genesis-wp/ac-161
async def test_fixed_grid(start_emulator, tmp_path, monkeypatch):
    from genesis_wp import ThermiaGenesis
    from genesis_wp.profiler import profile
    emu = await start_emulator(PROFILE_615)
    clock = Clock()
    update = ThermiaGenesis.async_update

    async def slow_read(self):
        result = await update(self)
        clock.seconds += 7
        return result

    monkeypatch.setattr(ThermiaGenesis, 'async_update', slow_read)
    path = tmp_path / 'grid.jsonl'
    await profile(emu.host, port=emu.port, model=WPE_I_KIND, interval=60,
                  duration=300, output=path, clock=clock)
    entries = lines(path)
    start = datetime.fromisoformat(entries[0]['started_at'])
    assert [(datetime.fromisoformat(s['ts']) - start).total_seconds()
            for s in entries[1:-1]] == [0, 60, 120, 180, 240]


# DHF-TEST: genesis-wp/requirement-40 genesis-wp/ac-160
async def test_failure_continues(start_emulator, tmp_path):
    from genesis_wp.profiler import profile
    emu = await start_emulator(PROFILE_615)

    class OutageClock(Clock):
        async def sleep(self, seconds):
            await super().sleep(seconds)
            if self.seconds == 60:
                await emu.stop()
            elif self.seconds == 120:
                await emu.start()

    path = tmp_path / 'failures.jsonl'
    await profile(emu.host, port=emu.port, model=WPE_I_KIND, interval=60,
                  samples=3, output=path, clock=OutageClock())
    entries = lines(path)
    assert [e['type'] for e in entries] == ['header', 'sample', 'failure', 'sample', 'trailer']
    assert entries[2]['error'] == 'ThermiaConnectionError'
    assert entries[2]['ts']
    assert entries[-1]['samples'] == 2 and entries[-1]['failures'] == 1


# DHF-TEST: genesis-wp/requirement-41 genesis-wp/ac-162 genesis-wp/ac-164
async def test_read_only_host_free_run(start_emulator, tmp_path, caplog):
    from genesis_wp.profiler import profile
    emu = await start_emulator(PROFILE_615)

    class OutageClock(Clock):
        async def sleep(self, seconds):
            await super().sleep(seconds)
            if self.seconds == 60:
                await emu.stop()
            elif self.seconds == 120:
                await emu.start()

    caplog.set_level('DEBUG')
    path = tmp_path / 'safe.jsonl'
    await profile('localhost', port=emu.port, model=WPE_I_KIND, interval=60,
                  samples=3, output=path, clock=OutageClock())
    assert [e['type'] for e in lines(path)] == ['header', 'sample', 'failure', 'sample', 'trailer']
    assert emu.requests and all(r.function_code in (1, 2, 3, 4) for r in emu.requests)
    assert not emu.writes
    assert 'localhost' not in path.read_text()
    for record in caplog.records:
        assert 'localhost' not in str(record.msg)
        assert 'localhost' not in str(record.args)


# DHF-TEST: genesis-wp/requirement-41 genesis-wp/ac-163
async def test_no_write_opt_in(start_emulator, tmp_path, monkeypatch):
    from genesis_wp import ThermiaGenesis
    from genesis_wp.profiler import profile
    emu = await start_emulator(PROFILE_615)
    kwargs = dict(port=emu.port, model=WPE_I_KIND, interval=60, samples=1,
                  output=tmp_path / 'readonly.jsonl', clock=Clock())
    with pytest.raises(TypeError):
        await profile(emu.host, read_only=False, **kwargs)
    assert not emu.requests
    update = ThermiaGenesis.async_update
    observed = []

    async def check_read_only(self):
        observed.append(self.read_only)
        return await update(self)

    monkeypatch.setattr(ThermiaGenesis, 'async_update', check_read_only)
    await profile(emu.host, **kwargs)
    assert observed == [True]


@pytest.mark.parametrize('options', [dict(interval=0, samples=1),
    dict(interval=float('nan'), samples=1), dict(interval=60),
    dict(interval=60, samples=1, duration=60), dict(interval=60, samples=1.5),
    dict(interval=60, samples=True), dict(interval=60, duration=-1)])
async def test_invalid_arguments_before_connect(tmp_path, options):
    from genesis_wp.profiler import profile
    with pytest.raises(ValueError):
        await profile('localhost', model=WPE_I_KIND, output=tmp_path / 'invalid', **options)
    assert not (tmp_path / 'invalid').exists()


async def test_initial_failure_and_append(start_emulator, tmp_path, monkeypatch):
    from genesis_wp import ThermiaGenesis
    from genesis_wp.profiler import profile
    emu = await start_emulator(PROFILE_615)
    update = ThermiaGenesis.async_update
    attempts = 0

    async def unavailable_first(self):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError('private target detail')
        return await update(self)

    monkeypatch.setattr(ThermiaGenesis, 'async_update', unavailable_first)
    path = tmp_path / 'append.jsonl'
    kwargs = dict(port=emu.port, model=WPE_I_KIND, interval=60, output=path, clock=Clock())
    await profile(emu.host, samples=2, **kwargs)
    entries = lines(path)
    assert entries[0]['firmware'] is None
    assert entries[1]['error'] == 'OSError'
    assert 'private target detail' not in path.read_text()
    assert entries[2]['type'] == 'sample'
    await profile(emu.host, duration=1, **kwargs)
    assert [e['type'] for e in lines(path)[4:]] == ['header', 'trailer']
    assert lines(path)[-1]['samples'] == 0
