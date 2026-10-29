"""The two test targets and the read-only live transport (requirement-25).

The live target is exercised against a loopback emulator standing in for the heat pump, on
127.0.0.2 so its address is distinct from everything else in the output. Session-level
behavior (opt-in, skipping, output hygiene) runs pytest in a subprocess.
"""

from __future__ import annotations

import asyncio
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from genesis_wp import GenesisReadOnlyError, ThermiaGenesis

from .conftest import TEST_CLASSES
from .harness import PROFILE_615, PROFILES, WPE_I_KIND
from .plant import CHECKS, WRITE_METHODS, ReadOnlyTransportError, ReadOnlyUnit, read_snapshot
from .targets import HOST_ENV, PORT_ENV, TARGET_ENV

pytestmark = pytest.mark.fixture

REPO = Path(__file__).resolve().parent.parent
STAND_IN = "127.0.0.2"  # loopback, but no other part of the suite uses it


def _env(**settings: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GENESIS_WP_")}
    env.update(settings)
    return env


async def _pytest(*args: str, **settings: str) -> tuple[int, str]:
    """Run pytest in a subprocess from the repo root; returns exit code and combined output."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pytest", "-o", "addopts=", "-p", "no:cacheprovider", *args,
        cwd=REPO, env=_env(**settings), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
    return proc.returncode, out.decode()


def _live(emu) -> dict[str, str]:
    return {TARGET_ENV: "live", HOST_ENV: emu.host, PORT_ENV: str(emu.port)}


# DHF-REQ: genesis-wp/requirement-25  DHF-TEST: genesis-wp/ac-94
async def test_without_opt_in_plant_read_tests_run_against_every_profile():
    code, out = await _pytest("--collect-only", "-q", "tests/test_plant_read.py")
    assert code == 0, out
    for check in CHECKS:
        for profile in PROFILES:
            assert f"test_plant[{profile}-{check.__name__}]" in out
    assert "[live-" not in out


# DHF-REQ: genesis-wp/requirement-25  DHF-TEST: genesis-wp/ac-95
async def test_plant_read_checks_hold_on_values_that_differ_from_the_capture(start_emulator):
    emu = await start_emulator(PROFILE_615, dynamics=True, seed=99, outdoor_temperature=-12.0)
    for _ in range(30):
        emu.step()
    snapshot = await read_snapshot("emulator", emu.host, emu.port, delay=0)
    assert snapshot.data["input_outdoor_temperature"] == -12.0  # the capture read 7.43 or so, not -12
    assert {check.__name__: check(snapshot) for check in CHECKS} == {check.__name__: [] for check in CHECKS}


# DHF-REQ: genesis-wp/requirement-25  DHF-TEST: genesis-wp/ac-96
@pytest.mark.parametrize("host", [None, "", "  "])
async def test_live_opt_in_without_a_host_stops_before_any_test(host):
    settings = {TARGET_ENV: "live"} if host is None else {TARGET_ENV: "live", HOST_ENV: host}
    code, out = await _pytest("tests/test_plant_read.py", **settings)
    assert code == pytest.ExitCode.USAGE_ERROR, out
    assert HOST_ENV in out
    assert "passed" not in out and "failed" not in out


async def test_unknown_target_is_a_usage_error():
    code, out = await _pytest("tests/test_plant_read.py", **{TARGET_ENV: "plant"})
    assert code == pytest.ExitCode.USAGE_ERROR, out
    assert TARGET_ENV in out


# DHF-REQ: genesis-wp/requirement-25  DHF-TEST: genesis-wp/ac-97
@pytest.mark.parametrize("method", sorted(WRITE_METHODS))
async def test_live_transport_refuses_every_write_before_connecting(start_emulator, method):
    emu = await start_emulator(PROFILE_615, host=STAND_IN)
    unit = ReadOnlyUnit(emu.host, emu.port)
    with pytest.raises(ReadOnlyTransportError):
        await getattr(unit, method)(5, 1)
    await unit.close()
    assert emu.connections == 0
    assert emu.requests == []


async def test_library_write_through_the_live_transport_is_refused_before_connecting(start_emulator, caplog):
    emu = await start_emulator(PROFILE_615, host=STAND_IN)
    unit = ReadOnlyUnit(emu.host, emu.port)
    client = ThermiaGenesis(kind=WPE_I_KIND, unit=unit, delay=0)
    with pytest.raises(GenesisReadOnlyError):
        await client.async_set("holding_comfort_wheel_setting", 21)
    # Second line: even a client opted in to writes cannot write through this unit. The
    # library logs a failed write instead of raising it.
    allowed = ThermiaGenesis(kind=WPE_I_KIND, unit=unit, delay=0, read_only=False)
    await allowed.async_set("holding_comfort_wheel_setting", 21)
    assert "ReadOnlyTransportError" in caplog.text
    await unit.close()
    assert emu.connections == 0
    assert emu.writes == []


async def test_live_transport_reads(start_emulator):
    emu = await start_emulator(PROFILE_615, host=STAND_IN)
    snapshot = await read_snapshot("live", emu.host, emu.port, delay=0)
    assert snapshot.firmware == emu.profile.firmware["software"]
    assert all(r.function_code in (1, 2, 3, 4) for r in emu.requests)
    assert STAND_IN not in repr(snapshot) and STAND_IN not in repr(ReadOnlyUnit(emu.host, emu.port))


# DHF-REQ: genesis-wp/requirement-25  DHF-TEST: genesis-wp/ac-98
async def test_live_run_executes_only_plant_read_tests(start_emulator, tmp_path):
    emu = await start_emulator(PROFILE_615, host=STAND_IN)
    report = tmp_path / "live.xml"
    code, out = await _pytest("tests", f"--junitxml={report}", "-q", **_live(emu))
    assert code == 0, out
    executed, skipped = [], {}
    for case in ET.parse(report).iter("testcase"):
        name = f"{case.get('classname')}::{case.get('name')}"
        skip = case.find("skipped")
        if skip is None:
            executed.append(name)
        else:
            skipped[name] = skip.get("message", "")
    assert executed and all(name.startswith("tests.test_plant_read::test_plant[live-") for name in executed)
    assert len(executed) == len(CHECKS)
    classes = {message.rsplit(": ", 1)[-1] for message in skipped.values()}
    assert all(message.startswith("not run on the live target: ") for message in skipped.values())
    assert classes <= set(TEST_CLASSES) - {"plant_read"}  # every skipped test names its class


FAILING = '''
import pytest

pytestmark = pytest.mark.plant_read


async def test_failing_assertion_on_a_plant_value(plant):
    assert plant.data["input_outdoor_temperature"] == 999.0, plant


async def test_failing_check(plant):
    assert ["deliberate violation"] == []
'''


# DHF-REQ: genesis-wp/requirement-25  DHF-TEST: genesis-wp/ac-99
async def test_live_run_output_never_contains_the_host(start_emulator, tmp_path):
    emu = await start_emulator(PROFILE_615, host=STAND_IN)
    (tmp_path / "test_deliberately_failing.py").write_text(FAILING)
    code, out = await _pytest(
        "-p", "tests.conftest", "--rootdir", str(tmp_path), "-o", "asyncio_mode=auto", "-vv", "-rA", "--log-level=DEBUG",
        str(tmp_path / "test_deliberately_failing.py"), **_live(emu),
    )
    assert code == pytest.ExitCode.TESTS_FAILED, out
    assert "2 failed" in out, out
    assert "genesis-wp test target: live" in out
    assert STAND_IN not in out


async def test_unreachable_live_host_fails_without_naming_it(tmp_path):
    (tmp_path / "test_deliberately_failing.py").write_text(FAILING)
    code, out = await _pytest(
        "-p", "tests.conftest", "--rootdir", str(tmp_path), "-o", "asyncio_mode=auto", "-vv", "-rA", "--log-level=DEBUG",
        str(tmp_path / "test_deliberately_failing.py"), **{TARGET_ENV: "live", HOST_ENV: STAND_IN, PORT_ENV: "9"},
    )
    assert code == pytest.ExitCode.TESTS_FAILED, out
    assert "ThermiaConnectionError" in out  # the library's host-free error over PlantConnectionError
    assert STAND_IN not in out
