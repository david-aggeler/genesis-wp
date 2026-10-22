"""Fixtures for emulator tests: emulator instances on free loopback ports and clients."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest
from pymodbus.client import AsyncModbusTcpClient

from genesis_wp.emulator import Emulator, get_profile

PROFILE_606 = "stiebel-wpe-i-07.1-plus-6.0.6"
PROFILE_615 = "stiebel-wpe-i-07.1-plus-6.1.5"
UNIT = 1


@pytest.fixture
async def start_emulator() -> AsyncIterator[Callable[..., Emulator]]:
    """Factory: ``await start_emulator(profile_name, **options)`` -> running Emulator."""
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
