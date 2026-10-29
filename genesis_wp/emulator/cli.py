"""genesis-wp-emulator: run one or more emulated heat pumps.

Examples::

    genesis-wp-emulator                                   # default profile on 127.0.0.1:5020
    genesis-wp-emulator -i stiebel-wpe-i-07.1-plus-6.0.6@5021 -i stiebel-wpe-i-07.1-plus-6.1.5@5022
    genesis-wp-emulator --dynamics --interval 5 -v
    genesis-wp-emulator --capability hot_water=on --capability heating_circuit_2=off
    genesis-wp-emulator --dynamics --outdoor-temperature -5
    genesis-wp-emulator --room-set-point-hc2 22.5
    genesis-wp-emulator --dynamics --interval 1 --climate climate.json --start 2026-01-15T06:00 --speed 60
    genesis-wp-emulator --dynamics --interval 1 --climate climate.json --control-port 5520
    genesis-wp-emulator ctl 127.0.0.1:5520 speed 3600
    genesis-wp-emulator --list-profiles
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import signal
import sys
from datetime import datetime
from pathlib import Path

from .capabilities import parse_selection
from .climate import ClimateError, ClimateProfile
from .control import ControlError, EmulatorControl
from .profile import ProfileError, get_profile, packaged_profiles
from .server import Emulator

DEFAULT_PROFILE = "stiebel-wpe-i-07.1-plus-6.1.5"
DEFAULT_PORT = 5020


def _instance(spec: str) -> tuple[str, int | None]:
    """Parse ``PROFILE[@PORT]``."""
    profile, _, port = spec.rpartition("@") if "@" in spec else (spec, "", "")
    if port and not port.isdigit():
        raise argparse.ArgumentTypeError(f"bad port in {spec!r}")
    return profile, int(port) if port else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="genesis-wp-emulator",
        description="Emulate Genesis-platform heat pumps over Modbus TCP from captured firmware profiles.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples::", 1)[1],
    )
    parser.add_argument(
        "-i", "--instance", action="append", type=_instance, metavar="PROFILE[@PORT]",
        help=f"profile name or JSON path, optionally @port; repeatable (default {DEFAULT_PROFILE}@{DEFAULT_PORT}). "
        "Instances without a port get consecutive ports from --port.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"first port (default {DEFAULT_PORT})")
    parser.add_argument("--dynamics", action="store_true", help="simulate drifting temperatures and compressor speed")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between dynamics steps (default 5)")
    parser.add_argument("--seed", type=int, default=0, help="random seed for dynamics (default 0)")
    parser.add_argument(
        "--capability", action="append", default=[], metavar="NAME=on|off",
        help="switch a plant capability on or off for every instance (library capability names, "
        "for example hot_water, pool, electric_meter); repeatable",
    )
    parser.add_argument(
        "--outdoor-temperature", type=float, metavar="CELSIUS",
        help="with --dynamics: hold the outdoor temperature of the heating model instead of letting it drift",
    )
    parser.add_argument(
        "--room-set-point-hc2", type=float, metavar="CELSIUS",
        help="start the HC2 room set point here; shifts the HC2 curve like the plant does. "
        "Emulator only: the WPE-I has no register for it",
    )
    parser.add_argument(
        "--climate", type=Path, metavar="FILE",
        help="with --dynamics: play this climate profile (genesis-wp-climate build) as outdoor temperature "
        "on a simulated clock",
    )
    parser.add_argument(
        "--start", type=datetime.fromisoformat, metavar="ISO-DATETIME",
        help="with --climate: simulated start, e.g. 2026-01-15T06:00 (default now)",
    )
    parser.add_argument(
        "--speed", type=float, default=1.0,
        help="with --climate: simulated seconds per real second, e.g. 60 = one hour per minute (default 1)",
    )
    parser.add_argument(
        "--control-port", type=int, metavar="PORT",
        help="serve the control channel (playback speed, pause, simulated time, held outdoor temperature) "
        "on PORT; instance n gets PORT+n, 0 = free ports. Drive it with: genesis-wp-emulator ctl HOST:PORT ...",
    )
    parser.add_argument("--ignore-writes", action="store_true", help="record writes but do not apply them")
    parser.add_argument("--list-profiles", action="store_true", help="list packaged profiles and exit")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="-v: requests, -vv: pymodbus debug")
    return parser


async def _run(args: argparse.Namespace) -> None:
    climate = ClimateProfile.load(args.climate) if args.climate else None
    emulators = _emulators(args, climate)
    for emu in emulators:
        await emu.start()
        _announce(emu, climate)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        for emu in emulators:
            await emu.stop()
            writes = len(emu.writes)
            print(f"{emu.profile.name}: {len(emu.requests)} requests, {writes} writes", flush=True)


def _emulators(args: argparse.Namespace, climate: ClimateProfile | None) -> list[Emulator]:
    """One emulator per --instance; instances without a port take the next one from --port."""
    specs = args.instance or [(DEFAULT_PROFILE, None)]
    emulators = []
    next_port = args.port
    for index, (name, port) in enumerate(specs):
        if port is None:
            port, next_port = next_port, next_port + 1
        control_port = args.control_port + index if args.control_port else args.control_port
        emulators.append(Emulator(
            get_profile(name),
            host=args.host,
            port=port,
            dynamics=args.dynamics,
            dynamics_interval=args.interval if args.dynamics else 0.0,
            seed=args.seed,
            apply_writes=not args.ignore_writes,
            capabilities=args.selection,
            outdoor_temperature=args.outdoor_temperature,
            initial_room_set_point_hc2=args.room_set_point_hc2,
            climate=climate,
            start_time=args.start,
            playback_speed=args.speed,
            control_port=control_port,
        ))
    return emulators


def _announce(emu: Emulator, climate: ClimateProfile | None) -> None:
    print(f"{emu.profile.name} (software {emu.profile.firmware['software']}) on {emu.host}:{emu.port}, unit {emu.profile.unit_id}", flush=True)
    for note in emu.profile.source.get("notes", []):
        if note.startswith("capability selection"):
            print(f"  {note}", flush=True)
    if emu.control_port is not None:
        print(f"  control channel on {emu.host}:{emu.control_port}", flush=True)
    if emu.simulated_time is not None:
        print(f"  climate {climate.bucket} profile, simulated start {emu.simulated_time:%Y-%m-%d %H:%M}, "
              f"speed {emu.playback_speed:g}", flush=True)


def build_ctl_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="genesis-wp-emulator ctl",
        description="Drive a running emulator over its control channel. Prints the result as JSON.",
    )
    parser.add_argument("endpoint", metavar="HOST:PORT", help="the emulator's control channel")
    parser.add_argument("--timeout", type=float, default=2.0, help="seconds per request (default 2)")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    commands.add_parser("identify", help="emulator, profile, protocol and package version")
    commands.add_parser("state", help="simulated time, speed, pause, outdoor temperature")
    commands.add_parser("speed", help="set the playback speed").add_argument("value", type=float)
    commands.add_parser("pause", help="pause the simulated clock")
    commands.add_parser("resume", help="resume the simulated clock")
    commands.add_parser("time", help="jump the simulated clock").add_argument("value", type=datetime.fromisoformat)
    commands.add_parser("hold", help="hold the outdoor temperature [°C]").add_argument("value", type=float)
    commands.add_parser("release", help="release the held outdoor temperature")
    return parser


async def _ctl(args: argparse.Namespace):
    host, _, port = args.endpoint.rpartition(":")
    if not host or not port.isdigit():
        raise ValueError(f"endpoint must be HOST:PORT, got {args.endpoint!r}")
    async with EmulatorControl(host, int(port), timeout=args.timeout) as control:
        call = {
            "identify": control.identify, "state": control.state, "pause": control.pause,
            "resume": control.resume, "release": control.release_outdoor,
            "speed": lambda: control.set_playback_speed(args.value),
            "time": lambda: control.set_simulated_time(args.value),
            "hold": lambda: control.hold_outdoor(args.value),
        }[args.command]
        return await call()


def ctl_main(argv: list[str]) -> int:
    args = build_ctl_parser().parse_args(argv)
    try:
        result = asyncio.run(_ctl(args))
    except ControlError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if dataclasses.is_dataclass(result):
        result = dataclasses.asdict(result)
    print(json.dumps(result if result is not None else {"ok": True}, default=str, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "ctl":
        return ctl_main(argv[1:])
    args = build_parser().parse_args(argv)
    if args.list_profiles:
        print("\n".join(packaged_profiles()))
        return 0
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if args.verbose >= 1:
        logging.getLogger("genesis_wp").setLevel(logging.DEBUG)
    if args.verbose >= 2:
        logging.getLogger("pymodbus").setLevel(logging.DEBUG)
    try:
        args.selection = parse_selection(args.capability)
        asyncio.run(_run(args))
    except (ProfileError, ClimateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:  # for example --climate without --dynamics, or --speed 0
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
