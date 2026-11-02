"""Offline recording operations, using recordings from the loopback emulator."""
from copy import deepcopy
import json
import socket

import pytest

from genesis_wp.const import KEY_SCALE, REGISTERS
from .harness import PROFILES, PROFILE_606, PROFILE_615, WPE_I_KIND
from .test_profiler import Clock, lines

pytestmark = pytest.mark.fixture


async def record(start_emulator, tmp_path, firmware=PROFILE_606, clock=None):
    from genesis_wp.profiler import profile
    emu = await start_emulator(firmware)
    path = tmp_path / 'recording.jsonl'
    await profile(emu.host, port=emu.port, model=WPE_I_KIND, interval=60,
                  samples=3, output=path, clock=clock or Clock())
    return path, emu


# DHF-TEST: genesis-wp/requirement-42 genesis-wp/ac-165
@pytest.mark.parametrize('firmware', PROFILES)
async def test_offline_decode(start_emulator, tmp_path, monkeypatch, firmware):
    from genesis_wp.profiler import load_recording, decode_again
    path, emu = await record(start_emulator, tmp_path, firmware)
    await emu.stop()

    def forbidden(*args, **kwargs):
        raise AssertionError('offline decoding attempted a connection')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    recording = load_recording(path)
    assert not recording.truncated
    assert decode_again(recording) == [s['decoded'] for s in lines(path)[1:-1]]


# DHF-TEST: genesis-wp/requirement-42 genesis-wp/ac-166
async def test_changed_scale(start_emulator, tmp_path):
    from genesis_wp.profiler import load_recording, decode_again
    path, _ = await record(start_emulator, tmp_path)
    registers = deepcopy(REGISTERS)
    registers['input_calculated_heating_demand'][KEY_SCALE] = 10
    expected = [s['decoded'] for s in lines(path)[1:-1]]
    for sample in expected:
        assert sample['input_calculated_heating_demand'] == -0.28
        sample['input_calculated_heating_demand'] = -2.8
    assert decode_again(load_recording(path), registers) == expected


# DHF-TEST: genesis-wp/requirement-42 genesis-wp/ac-167
async def test_compare_changed_samples(start_emulator, tmp_path, monkeypatch):
    from genesis_wp.profiler import profile, load_recording, compare_with_profile
    from genesis_wp.emulator import Emulator
    action = Emulator._action
    changed = False

    async def changed_words(self, fc, start, address, count, registers, values):
        await action(self, fc, start, address, count, registers, values)
        if changed:
            for code, target, word in [(4, 30, 100), (3, 5, 215)]:
                if fc == code and address <= target < address + count:
                    registers[target - start] = word

    monkeypatch.setattr(Emulator, '_action', changed_words)
    emu = await start_emulator(PROFILE_615)
    original = emu.profile

    class ChangeClock(Clock):
        async def sleep(self, seconds):
            nonlocal changed
            await super().sleep(seconds)
            changed = self.seconds >= 60

    path = tmp_path / 'diff.jsonl'
    await profile(emu.host, port=emu.port, model=WPE_I_KIND, interval=60,
                  samples=3, output=path, clock=ChangeClock())
    differences = compare_with_profile(load_recording(path), original)
    expected = {('input_registers', 30, 'input_calculated_heating_demand', 100, 71),
                ('holding_registers', 5, 'holding_comfort_wheel_setting', 215,
                 original.blocks['holding_registers'].value(5))}
    assert differences[0] == []
    assert set(differences[1]) == set(differences[2]) == expected


async def test_loader_prefix_and_validation(start_emulator, tmp_path):
    from genesis_wp.profiler import load_recording, decode_again
    path, _ = await record(start_emulator, tmp_path)
    entries = lines(path)
    entries[2] = {'type': 'failure', 'ts': entries[2]['ts'], 'error': 'OSError'}
    path.write_text(''.join(json.dumps(e) + '\n' for e in entries[:-1]))
    recording = load_recording(path)
    assert recording.truncated and recording.trailer is None
    assert len(recording.failures) == 1 and len(decode_again(recording)) == 2
    for key, value in [('format_version', 2), ('model', 'unknown')]:
        bad = deepcopy(entries)
        bad[0][key] = value
        path.write_text(''.join(json.dumps(e) + '\n' for e in bad))
        with pytest.raises(ValueError):
            decode_again(load_recording(path))


@pytest.mark.parametrize('content', ['', 'null\n', '{"type":"header","format_version":true}\n',
    '{"type":"header","format_version":1,"model":"unknown"}\n'])
def test_invalid_header(tmp_path, content):
    from genesis_wp.profiler import load_recording
    path = tmp_path / 'invalid.jsonl'
    path.write_text(content)
    with pytest.raises(ValueError):
        load_recording(path)


@pytest.mark.parametrize('entry', [None, {'type': 'other'},
    {'type': 'sample'}, {'type': 'sample', 'raw': {'unknown': {}}},
    {'type': 'sample', 'raw': {'input_registers': {'30': True}}},
    {'type': 'sample', 'raw': {'input_registers': {'-1': 0}}}])
def test_invalid_entry(tmp_path, entry):
    from genesis_wp.profiler import load_recording
    path = tmp_path / 'invalid.jsonl'
    header = dict(type='header', format_version=1, model=WPE_I_KIND)
    path.write_text(json.dumps(header) + '\n' + json.dumps(entry) + '\n')
    with pytest.raises(ValueError):
        load_recording(path)


async def test_model_mismatch_and_trailing_entry(start_emulator, tmp_path):
    from dataclasses import replace
    from genesis_wp.profiler import load_recording, decode_again, compare_with_profile
    path, emu = await record(start_emulator, tmp_path)
    recording = load_recording(path)
    with pytest.raises(ValueError, match='model'):
        decode_again(recording, {})
    with pytest.raises(ValueError, match='model'):
        compare_with_profile(recording, replace(emu.profile, model='inverter'))
    with path.open('a') as stream:
        stream.write('{"type":"trailer"}\n')
    with pytest.raises(ValueError, match='after'):
        load_recording(path)
