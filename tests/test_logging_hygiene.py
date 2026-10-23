"""The device host never reaches logs, stdout or exception text (requirement-9).

The emulator's own bind-address line (logger ``genesis_wp.emulator``) is local test
topology, not the device's, and is excluded.
"""

from __future__ import annotations

import logging

import pytest

from genesis_wp import ThermiaConnectionError, ThermiaGenesis
from genesis_wp.const import ATTR_HOLDING_COMFORT_WHEEL_SETTING

from .harness import PROFILE_615, WPE_I_KIND
from .test_transport import FIRMWARE, _closed_port

HOSTS = ["127.0.0.1", "localhost"]
FORBIDDEN = ("127.0.0.1", "localhost", "::1")  # "localhost" resolves to both loopback addresses
_FORMATTER = logging.Formatter()


def _library_records(caplog):
    return [
        r for r in caplog.records
        if r.name.startswith("genesis_wp") and not r.name.startswith("genesis_wp.emulator")
    ]


def _record_text(record: logging.LogRecord) -> str:
    text = f"{record.getMessage()} {record.args!r}"
    if record.exc_info:
        text += _FORMATTER.formatException(record.exc_info)
    return text


def assert_no_host_in_logs(caplog):
    for record in _library_records(caplog):
        text = _record_text(record).lower()
        for host in FORBIDDEN:
            assert host not in text, f"{record.name} {record.levelname}: {text}"


def assert_no_host_in_chain(err: BaseException):
    seen, stack = set(), [err]
    while stack:
        e = stack.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        text = f"{e!s} {e!r} {getattr(e, 'message', '')}".lower()
        for host in FORBIDDEN:
            assert host not in text, f"{type(e).__name__}: {text}"
        stack += [e.__cause__, e.__context__]


@pytest.fixture
def debug_caplog(caplog):
    caplog.set_level(logging.DEBUG)
    return caplog


# DHF-REQ: genesis-wp/requirement-9  DHF-TEST: genesis-wp/ac-25
# DHF-REQ: genesis-wp/requirement-9  DHF-TEST: genesis-wp/ac-26
@pytest.mark.parametrize("host", HOSTS)
async def test_update_then_lost_connection(start_emulator, debug_caplog, capsys, host):
    emu = await start_emulator(PROFILE_615)
    client = ThermiaGenesis(host, port=emu.port, kind=WPE_I_KIND, delay=0, timeout=1)
    try:
        assert await client.async_update()
        await emu.stop()
        with pytest.raises(ThermiaConnectionError) as info:
            await client.async_update()
    finally:
        await client.close()
    assert "unit 1" in info.value.message
    assert_no_host_in_chain(info.value)
    assert_no_host_in_logs(debug_caplog)
    assert capsys.readouterr().out == ""


# DHF-REQ: genesis-wp/requirement-9  DHF-TEST: genesis-wp/ac-25
# DHF-REQ: genesis-wp/requirement-9  DHF-TEST: genesis-wp/ac-26
@pytest.mark.parametrize("host", HOSTS)
async def test_unreachable_host(debug_caplog, capsys, host):
    client = ThermiaGenesis(host, port=_closed_port(), kind=WPE_I_KIND, delay=0, timeout=1)
    with pytest.raises(ThermiaConnectionError) as info:
        await client.async_update(only_registers=FIRMWARE)
    await client.close()
    assert "unit 1" in info.value.message
    assert_no_host_in_chain(info.value)
    assert_no_host_in_logs(debug_caplog)
    assert capsys.readouterr().out == ""


def _assert_logged_without_stdout(caplog, capsys):
    assert capsys.readouterr().out == ""
    records = _library_records(caplog)
    errors = [r for r in records if r.levelno >= logging.ERROR]
    assert errors and all(not r.exc_info for r in errors)  # traceback only at DEBUG
    assert [r for r in records if r.exc_info and r.levelno == logging.DEBUG]
    assert all(r.levelno == logging.DEBUG for r in records if r.exc_info)
    assert_no_host_in_logs(caplog)


# DHF-REQ: genesis-wp/requirement-9  DHF-TEST: genesis-wp/ac-27
async def test_unexpected_read_error_is_logged_not_printed(start_emulator, debug_caplog, capsys, monkeypatch):
    emu = await start_emulator(PROFILE_615)
    client = ThermiaGenesis(emu.host, port=emu.port, kind=WPE_I_KIND, delay=0)
    try:
        unit = await client._ensure_unit()

        async def broken(*args, **kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(unit, "read_input_registers", broken)
        await client.async_update(only_registers=FIRMWARE)
    finally:
        await client.close()
    _assert_logged_without_stdout(debug_caplog, capsys)


# DHF-REQ: genesis-wp/requirement-9  DHF-TEST: genesis-wp/ac-27
async def test_unexpected_write_error_is_logged_not_printed(start_emulator, debug_caplog, capsys, monkeypatch):
    emu = await start_emulator(PROFILE_615)
    client = ThermiaGenesis(emu.host, port=emu.port, kind=WPE_I_KIND, delay=0, read_only=False)
    try:
        unit = await client._ensure_unit()

        async def broken(*args, **kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(unit, "write_register", broken)
        await client.async_set(ATTR_HOLDING_COMFORT_WHEEL_SETTING, 21)
    finally:
        await client.close()
    assert emu.writes == []
    _assert_logged_without_stdout(debug_caplog, capsys)
