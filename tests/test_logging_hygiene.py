"""The device host never reaches logs, stdout or exception text (requirement-9).

Every logger counts, including the transport's (``pymodbus.logging``, issue-16). The
emulator's own bind-address line (logger ``genesis_wp.emulator``) is local test topology,
not the device's, and is excluded.
"""

from __future__ import annotations

import logging

import pytest

from genesis_wp import ThermiaConnectionError, ThermiaGenesis, redact_transport_host
from genesis_wp.const import ATTR_HOLDING_COMFORT_WHEEL_SETTING

from .harness import PROFILE_615, WPE_I_KIND
from .test_transport import FIRMWARE, _closed_port

pytestmark = pytest.mark.writes

HOSTS = ["127.0.0.1", "localhost"]
FORBIDDEN = ("127.0.0.1", "localhost", "::1")  # "localhost" resolves to both loopback addresses
_FORMATTER = logging.Formatter()


def _library_records(caplog):
    return [r for r in caplog.records if not r.name.startswith("genesis_wp.emulator")]


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
    records = [r for r in _library_records(caplog) if r.name.startswith("genesis_wp")]
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


def _transport_records(caplog, level):
    return [r for r in caplog.records if r.name.startswith("pymodbus") and r.levelno == level]


# DHF-REQ: genesis-wp/requirement-9  DHF-TEST: genesis-wp/ac-100
@pytest.mark.parametrize("host", HOSTS)
async def test_failed_connect_warning_is_redacted_not_dropped(debug_caplog, host):
    client = ThermiaGenesis(host, port=_closed_port(), kind=WPE_I_KIND, delay=0, timeout=1)
    with pytest.raises(ThermiaConnectionError):
        await client.async_update(only_registers=FIRMWARE)
    await client.close()
    warnings = _transport_records(debug_caplog, logging.WARNING)
    assert warnings, "the transport's failed-connect WARNING must still be emitted"
    assert any("<device>" in r.getMessage() for r in warnings)
    assert_no_host_in_logs(debug_caplog)


# DHF-REQ: genesis-wp/requirement-9  DHF-TEST: genesis-wp/ac-100
async def test_connect_debug_record_is_redacted(start_emulator, debug_caplog):
    emu = await start_emulator(PROFILE_615)
    client = ThermiaGenesis(emu.host, port=emu.port, kind=WPE_I_KIND, delay=0, timeout=1)
    try:
        assert await client.async_update(only_registers=FIRMWARE)
    finally:
        await client.close()
    connects = [r for r in _transport_records(debug_caplog, logging.DEBUG) if "Connecting to" in r.getMessage()]
    assert connects and all("<device>" in r.getMessage() for r in connects)
    assert_no_host_in_logs(debug_caplog)


def test_redaction_ends_when_the_last_holder_releases_it(caplog):
    caplog.set_level(logging.DEBUG)
    transport = logging.getLogger("pymodbus.logging")
    first, second = redact_transport_host("10.9.8.7"), redact_transport_host("10.9.8.7")
    first.remove()
    first.remove()  # idempotent: does not release the second holder's share
    transport.warning("Failed to connect ('10.9.8.7', 502)")
    second.remove()
    transport.warning("after ('10.9.8.7', 502)")
    texts = [r.getMessage() for r in caplog.records if r.name == "pymodbus.logging"]
    assert texts == ["Failed to connect ('<device>', 502)", "after ('10.9.8.7', 502)"]


def test_redaction_covers_args_and_exception_text(caplog):
    caplog.set_level(logging.DEBUG)
    transport = logging.getLogger("pymodbus.logging")
    handle = redact_transport_host("plant.example", "10.9.8.7")
    try:
        try:
            raise OSError("connect to plant.example (10.9.8.7) refused")
        except OSError:
            transport.warning("Failed to connect %s", "plant.example:502", exc_info=True)
    finally:
        handle.remove()
    (record,) = [r for r in caplog.records if r.name == "pymodbus.logging"]
    text = _record_text(record)
    assert "plant.example" not in text and "10.9.8.7" not in text
    assert "Failed to connect <device>:502" in text and "refused" in text


async def test_two_clients_redact_their_own_hosts(start_emulator, debug_caplog):
    emu = await start_emulator(PROFILE_615)
    clients = [ThermiaGenesis(host, port=emu.port, kind=WPE_I_KIND, delay=0, timeout=1) for host in HOSTS]
    try:
        for client in clients:
            assert await client.async_update(only_registers=FIRMWARE)
        await clients[0].close()  # the other client still holds its redaction
        logging.getLogger("pymodbus.logging").warning("late record for localhost")
    finally:
        for client in clients:
            await client.close()
    assert_no_host_in_logs(debug_caplog)
