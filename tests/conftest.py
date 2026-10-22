"""Suite-wide test setup: loopback-only network guard and emulator fixtures.

The guard (requirement-7, ac-17) refuses every socket connection, datagram and name
lookup that is not loopback, for the whole pytest process. Tests must never reach a real
heat pump, a DNS server or the internet. Subprocesses a test starts are not patched; they
must be pointed at loopback by the test itself.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import AsyncIterator, Callable

import pytest
from pymodbus.client import AsyncModbusTcpClient

from genesis_wp.emulator import Emulator, get_profile

from .harness import PROFILE_615


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


_install_guard()


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
