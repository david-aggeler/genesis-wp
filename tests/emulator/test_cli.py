"""genesis-wp-emulator command line."""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

from pymodbus.client import AsyncModbusTcpClient

from genesis_wp.emulator.cli import _instance, build_parser, main
from genesis_wp.emulator.server import _free_port

from ..harness import PROFILE_606, PROFILE_615, UNIT


def test_instance_spec():
    assert _instance("name") == ("name", None)
    assert _instance("name@5021") == ("name", 5021)


def test_list_profiles(capsys):
    assert main(["--list-profiles"]) == 0
    assert capsys.readouterr().out.split() == [PROFILE_606, PROFILE_615]


def test_unknown_profile_fails(capsys):
    assert main(["-i", "no-such-profile"]) == 2
    assert "unknown profile" in capsys.readouterr().err


def test_climate_needs_dynamics(tmp_path, capsys):
    from datetime import date

    from genesis_wp.emulator.climate import LICENSE, SOURCE, ClimateProfile

    path = tmp_path / "c.json"
    ClimateProfile("month", date(2021, 1, 1), date(2025, 12, 31), 0.0, 0.0, "UTC", SOURCE, LICENSE,
                   ((0.0,) * 24,) * 12).save(path)
    args = build_parser().parse_args(["--climate", str(path), "--start", "2026-01-15T06:00", "--speed", "60"])
    assert (args.start.hour, args.speed) == (6, 60)
    assert main(["--climate", str(path), "--port", "0"]) == 2
    assert "needs dynamics" in capsys.readouterr().err


def test_hc2_room_set_point_needs_profile_rules(capsys):
    assert build_parser().parse_args(["--room-set-point-hc2", "22.5"]).room_set_point_hc2 == 22.5
    assert main(["-i", PROFILE_606, "--room-set-point-hc2", "22"]) == 2
    assert "no room_set_points rule for hc2" in capsys.readouterr().err


async def test_command_runs_two_instances():
    """DHF-REQ: genesis-wp/requirement-8 — runnable as genesis-wp-emulator, several instances."""
    ports = [_free_port("127.0.0.1"), _free_port("127.0.0.1")]
    command = Path(sys.executable).parent / "genesis-wp-emulator"  # installed console script
    assert command.exists(), "install the package (scripts/setup-as-user.sh) to get genesis-wp-emulator"
    proc = await asyncio.create_subprocess_exec(
        str(command),
        "-i", f"{PROFILE_606}@{ports[0]}", "-i", f"{PROFILE_615}@{ports[1]}",
        stdout=asyncio.subprocess.PIPE,
    )
    try:
        for _ in ports:  # one "listening" line per instance
            await asyncio.wait_for(proc.stdout.readline(), timeout=10)
        versions = []
        for port in ports:
            client = AsyncModbusTcpClient("127.0.0.1", port=port, timeout=2, retries=0)
            assert await client.connect()
            versions.append((await client.read_input_registers(311, count=3, device_id=UNIT)).registers)
            client.close()
        assert versions == [[6, 0, 6], [6, 1, 5]]
    finally:
        proc.send_signal(signal.SIGTERM)
        await asyncio.wait_for(proc.wait(), timeout=10)
    assert proc.returncode == 0
