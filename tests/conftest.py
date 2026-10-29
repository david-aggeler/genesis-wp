"""Suite-wide test setup: network guard, test target, test classes and emulator fixtures.

The guard (requirement-7, ac-17) refuses every socket connection, datagram and name
lookup that is not loopback, for the whole pytest process. Tests must never reach a DNS
server, the internet or, by default, a real heat pump. Subprocesses a test starts are not
patched; they must be pointed at loopback by the test itself.

Test target (requirement-25, ``tests/targets.py``): the emulator by default. On the owner's
live opt-in the guard additionally allows exactly the heat pump's resolved address and port,
the plant read tests run against it read-only, and every other test is skipped with its
class as the reason. Every test module declares its class with a marker (TEST_CLASSES).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from collections.abc import AsyncIterator, Callable

import pytest
from pymodbus.client import AsyncModbusTcpClient

from genesis_wp.emulator import Emulator, get_profile

from .harness import PROFILE_615, PROFILES
from .plant import Snapshot, read_snapshot
from .targets import LIVE, TargetError, selected

#: Test classes (requirement-25). Only plant_read runs on the live target.
TEST_CLASSES = {
    "plant_read": "invariants that hold on any healthy plant; runs on the emulator and the real heat pump",
    "fixture": "compares against captured profile values or uses an emulator scenario",
    "writes": "writes registers",
    "dynamics": "needs emulator dynamics or the simulated clock",
    "emulator": "tests the emulator itself",
    "emulator_control": "uses the emulator control channel",
    "unit": "needs no device",
}


class NetworkGuardError(OSError):
    """Raised instead of a non-loopback network operation during tests."""


def _is_loopback_host(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode()
    if not isinstance(host, str):
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False  # a name other than localhost would need a DNS lookup


def _check_address(sock: socket.socket, address) -> None:
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        return  # AF_UNIX and friends never leave the host
    if (address[0], address[1]) in _LIVE_ADDRESSES:
        return  # the owner's live opt-in: exactly the heat pump's address and port
    if not _is_loopback_host(address[0]):
        raise NetworkGuardError(f"test network guard: only loopback is allowed, refused {address!r}")


def _install_guard() -> None:
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_sendto = socket.socket.sendto
    real_getaddrinfo = socket.getaddrinfo

    def connect(self, address):
        _check_address(self, address)
        return real_connect(self, address)

    def connect_ex(self, address):
        _check_address(self, address)
        return real_connect_ex(self, address)

    def sendto(self, data, *args):
        _check_address(self, args[-1])
        return real_sendto(self, data, *args)

    def getaddrinfo(host, *args, **kwargs):
        if host is not None and not _is_loopback_host(host):
            raise NetworkGuardError(f"test network guard: only loopback is allowed, refused lookup of {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto
    socket.getaddrinfo = getaddrinfo


def _resolve_live() -> frozenset[tuple[str, int]]:
    """The live target's addresses, resolved once before the guard is installed."""
    if LIVE_TARGET is None:
        return frozenset()
    try:
        infos = socket.getaddrinfo(LIVE_TARGET.host, LIVE_TARGET.port, type=socket.SOCK_STREAM)
    except OSError as err:
        raise TargetError(f"cannot resolve the live host ({type(err).__name__})") from None
    return frozenset((info[4][0], LIVE_TARGET.port) for info in infos)


def _install_redaction(secrets: frozenset[str]) -> None:
    """Replace the live host in every log record, whoever logs it.

    pymodbus logs the address it connects to; requirement-9 covers only genesis_wp's own
    records, so the live run redacts at record creation, before any handler sees it.
    """
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        text = record.getMessage()
        if record.exc_info:
            text += "\n" + logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
        if any(secret in text for secret in secrets):
            for secret in secrets:
                text = text.replace(secret, "<live host>")
            record.msg, record.args = text, ()
        return record

    logging.setLogRecordFactory(factory)


try:
    LIVE_TARGET = selected()
    _LIVE_ADDRESSES = _resolve_live()
    TARGET_ERROR = None
except TargetError as err:
    LIVE_TARGET, _LIVE_ADDRESSES, TARGET_ERROR = None, frozenset(), str(err)

_install_guard()
if LIVE_TARGET is not None:
    _install_redaction(frozenset({LIVE_TARGET.host, *(address for address, _ in _LIVE_ADDRESSES)}))


def pytest_configure(config):
    for name, description in TEST_CLASSES.items():
        config.addinivalue_line("markers", f"{name}: {description}")
    if TARGET_ERROR is not None:
        raise pytest.UsageError(TARGET_ERROR)


def pytest_report_header(config):
    return f"genesis-wp test target: {LIVE if LIVE_TARGET else 'emulator'}"


def pytest_collection_modifyitems(config, items):
    """On the live target, run plant read tests only; skip everything else naming its class."""
    if LIVE_TARGET is None:
        return
    for item in items:
        if item.get_closest_marker("plant_read"):
            continue
        cls = next((name for name in TEST_CLASSES if item.get_closest_marker(name)), "unclassified")
        item.add_marker(pytest.mark.skip(reason=f"not run on the live target: {cls}"))


def pytest_generate_tests(metafunc):
    if "plant" in metafunc.fixturenames:
        metafunc.parametrize("plant", [LIVE] if LIVE_TARGET else list(PROFILES), indirect=True)


_SNAPSHOTS: dict[str, Snapshot | BaseException] = {}


async def _take_snapshot(target: str) -> Snapshot:
    if target == LIVE:
        host, port = sorted(_LIVE_ADDRESSES)[0]
        return await read_snapshot(LIVE, host, port)
    emu = Emulator(get_profile(target), port=0)
    await emu.start()
    try:
        return await read_snapshot(target, emu.host, emu.port, delay=0)
    finally:
        await emu.stop()


@pytest.fixture
async def plant(request) -> Snapshot:
    """One full read-only update of the selected target, taken once per session and target.

    The real heat pump is read once per run, about as many requests as a probe.
    """
    target = request.param
    if target not in _SNAPSHOTS:
        try:
            _SNAPSHOTS[target] = await _take_snapshot(target)
        except Exception as err:  # noqa: BLE001 - remembered so one failed read is not retried per test
            _SNAPSHOTS[target] = err
    snapshot = _SNAPSHOTS[target]
    if isinstance(snapshot, BaseException):
        raise snapshot
    return snapshot


@pytest.fixture
async def start_emulator() -> AsyncIterator[Callable[..., Emulator]]:
    """Factory: ``await start_emulator(profile_name, **options)`` -> running Emulator on a free loopback port."""
    running: list[Emulator] = []

    async def start(profile: str = PROFILE_615, **options) -> Emulator:
        emu = Emulator(get_profile(profile), port=0, **options)
        await emu.start()
        running.append(emu)
        return emu

    yield start
    for emu in running:
        await emu.stop()


@pytest.fixture
async def connect() -> AsyncIterator[Callable[[Emulator], AsyncModbusTcpClient]]:
    """Factory: ``await connect(emulator)`` -> connected pymodbus client."""
    clients: list[AsyncModbusTcpClient] = []

    async def open_client(emu: Emulator) -> AsyncModbusTcpClient:
        client = AsyncModbusTcpClient(emu.host, port=emu.port, timeout=2, retries=0)
        assert await client.connect()
        clients.append(client)
        return client

    yield open_client
    for client in clients:
        client.close()
