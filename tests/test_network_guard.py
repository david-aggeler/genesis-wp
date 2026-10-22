"""The suite may only talk to the loopback interface (requirement-7)."""

from __future__ import annotations

import asyncio
import socket

import pytest

# RFC 5737 TEST-NET-1: documentation range, never routed. The guard must refuse it
# before any packet leaves, so these tests send nothing even if the guard is broken
# only in the "refuse" direction (connect would then fail with a timeout/unreachable).
OUTSIDE = ("192.0.2.1", 502)


def _blocked(exc: BaseException) -> bool:
    return "loopback" in str(exc)


# DHF-REQ: genesis-wp/requirement-7  DHF-TEST: genesis-wp/ac-17
def test_tcp_connect_outside_loopback_is_refused():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        with pytest.raises(OSError) as info:
            sock.connect(OUTSIDE)
    assert _blocked(info.value)


def test_connect_ex_and_udp_outside_loopback_are_refused():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(OSError) as info:
            sock.connect_ex(OUTSIDE)
        assert _blocked(info.value)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        with pytest.raises(OSError) as info:
            sock.sendto(b"x", OUTSIDE)
        assert _blocked(info.value)


async def test_asyncio_connection_outside_loopback_is_refused():
    with pytest.raises(OSError) as info:
        await asyncio.wait_for(asyncio.open_connection(*OUTSIDE), timeout=1)
    assert _blocked(info.value)


def test_hostname_resolving_outside_loopback_is_refused():
    with pytest.raises(OSError) as info:
        socket.create_connection(("example.com", 80), timeout=0.5)
    assert _blocked(info.value)


def test_loopback_is_allowed():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        for host in ("127.0.0.1", "localhost", "LOCALHOST"):
            with socket.create_connection((host, port), timeout=1):
                pass
            server.accept()[0].close()
