"""Test transport scheduling allowance and bounded failure on loopback."""

import asyncio
from types import SimpleNamespace

import pytest

from genesis_wp import ThermiaConnectionError
from genesis_wp.emulator import get_profile

from .harness import PROFILE_615, expected_library_values, library_full_update

pytestmark = pytest.mark.fixture


async def _relay(reader, writer):
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()


@pytest.fixture
async def loopback_endpoint():
    """Own accepted tasks and streams, including cleanup after a timeout."""
    tasks, writers = [], []

    async def start(handler):
        async def accept(reader, writer):
            writers.append(writer)
            tasks.append(asyncio.current_task())
            await handler(reader, writer, writers)

        server = await asyncio.start_server(accept, "127.0.0.1", 0)
        servers.append(server)
        return SimpleNamespace(host="127.0.0.1", port=server.sockets[0].getsockname()[1],
                               profile=get_profile(PROFILE_615))

    servers = []
    yield start
    for server in servers:
        server.close()
    for writer in writers:
        writer.close()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.gather(*(writer.wait_closed() for writer in writers), return_exceptions=True)
    for server in servers:
        await server.wait_closed()


# DHF-TEST: genesis-wp/requirement-36
async def test_harness_tolerates_a_delayed_emulator_response(start_emulator, loopback_endpoint):
    emu = await start_emulator(PROFILE_615)

    async def delayed(reader, writer, writers):
        upstream_reader, upstream_writer = await asyncio.open_connection(emu.host, emu.port)
        writers.append(upstream_writer)
        # Model scheduling delay beyond the production default without CPU load.
        await asyncio.sleep(6)
        async with asyncio.TaskGroup() as group:
            group.create_task(_relay(reader, upstream_writer))
            group.create_task(_relay(upstream_reader, writer))

    endpoint = await loopback_endpoint(delayed)
    data = await asyncio.wait_for(library_full_update(endpoint), timeout=30)
    assert data == expected_library_values(PROFILE_615)


# DHF-TEST: genesis-wp/requirement-36  DHF-TEST: genesis-wp/ac-147
async def test_harness_fails_in_bounded_time_when_server_never_answers(loopback_endpoint):
    received = asyncio.Event()

    async def silent(reader, writer, writers):
        assert await reader.read(65536)
        received.set()
        await reader.read()

    endpoint = await loopback_endpoint(silent)
    # The outer deadline must raise TimeoutError if the client's own timeout stops working.
    with pytest.raises(ThermiaConnectionError):
        await asyncio.wait_for(library_full_update(endpoint), timeout=30)
    assert received.is_set()
