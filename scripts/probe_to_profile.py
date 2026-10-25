#!/usr/bin/env python3
"""Convert a read-only probe capture into a genesis-wp emulator firmware profile.

The probe (homelab, 2026-10-22) ran the inherited pythermiagenesis17 library (now genesis_wp) against the
real heat pump and saved:

- ``library_values.json``: decoded values keyed by library register name
- ``summary.json``: run metadata plus raw values of a few extra reads

This script turns that into a profile (see ``genesis_wp/emulator/profile.py``):

- **ranges**: the address spans the unit answered without a Modbus exception: the
  probe's own read requests (frozen in ``PROBE_READS``), the extra raw reads, and any
  supplements from later read-only captures.
- **values**: raw register values. Decoded library values are converted back to raw
  (scale, int16 two's complement, 32-bit word order, status enum); extra reads are
  already raw.

Only register facts are written. Host names, aliases and other identifiers in the capture
are dropped, and the run aborts if ``summary.json`` carries a field it does not know.

Usage::

    .venv/bin/python scripts/probe_to_profile.py PROBE_DIR \\
        --software 6.1.5 --controller 17.3.145 --captured 2026-10-22 \\
        --out genesis_wp/emulator/profiles/stiebel-wpe-i-07.1-plus-6.1.5.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from genesis_wp.const import (
    KEY_ADDRESS,
    KEY_DATATYPE,
    KEY_REG_TYPE,
    KEY_SCALE,
    MODEL_STIEBEL_WPE_I_071,
    REG_COIL,
    REG_DISCRETE_INPUT,
    REG_HOLDING,
    REG_INPUT,
    REGISTERS,
    TYPE_BIT,
    TYPE_INT,
    TYPE_LONG,
    TYPE_LONG_LE,
    TYPE_STATUS,
    TYPE_UINT,
)

KIND = MODEL_STIEBEL_WPE_I_071
# The probe ran ThermiaGenesis(..., delay=0.1, max_registers=16); same chunking here.
MAX_REGISTERS = 16

BLOCK_OF = {
    REG_COIL: "coils",
    REG_DISCRETE_INPUT: "discrete_inputs",
    REG_HOLDING: "holding_registers",
    REG_INPUT: "input_registers",
}

# Inverse of the status decoding in genesis_wp ThermiaGenesis._get_data. "OFF" is also what the
# library returns for unknown raw values, so a decoded "OFF" maps back to 0.
STATUS_RAW = {
    "OFF": 0,
    "Manual Operation": 1,
    "Defrost": 2,
    "Hot water": 3,
    "Heat": 4,
    "Cool": 5,
    "Pool": 6,
    "Anti legionella": 7,
    "Standby": 98,
    "No demand": 99,
}

# summary.json extras: label -> (block, first address). Raw values, read directly.
EXTRAS = {
    "dinput_247_254_ksm": ("discrete_inputs", 247),
    "input_413_fan_test": ("input_registers", 413),
    "input_157_158_power": ("input_registers", 157),
    "holding_0_operational_mode": ("holding_registers", 0),
}

# summary.json fields we understand. Anything else aborts the conversion (identifier guard).
SUMMARY_FIELDS = {
    "host_alias",  # dropped
    "kind",
    "started",
    "lib_registers_flagged",
    "pass1_seconds",
    "pass1_values",
    "pass1_complete",
    "firmware",
    "pass2_failed",
    "extras",
}

# Registers that move when the emulator's plant dynamics are on: raw bounds around the
# captured value and the maximum raw change per step. Plausibility only, not a plant model.
DYNAMICS = {
    "input_compressor_speed_rpm": (300, 25),
    "input_condenser_in_temperature": (300, 10),
    "input_condenser_out_temperature": (300, 10),
    "input_brine_in_temperature": (200, 5),
    "input_brine_out_temperature": (200, 5),
}


# The 35 read requests the probe actually sent (pythermiagenesis17 v0.3.0, model
# stiebel_wpe_i_07_1, max_registers=16), as (function code, start, count). All answered
# without exception on both firmwares. Frozen here because the library's own read plan
# changes over time; the capture does not.
PROBE_READS = [
    (1, 3, 12), (1, 20, 16), (1, 37, 6),
    (2, 0, 4), (2, 9, 8), (2, 22, 15), (2, 47, 14), (2, 66, 14), (2, 83, 4), (2, 199, 13), (2, 218, 8), (2, 235, 12),
    (4, 1, 1), (4, 4, 16), (4, 20, 7), (4, 36, 16), (4, 52, 16), (4, 68, 17), (4, 106, 16), (4, 122, 16), (4, 139, 9),
    (4, 160, 4), (4, 157, 2), (4, 311, 9),
    (3, 0, 13), (3, 16, 1), (3, 22, 9), (3, 49, 2), (3, 58, 13), (3, 75, 14), (3, 105, 11), (3, 299, 6), (3, 117, 9),
    (3, 298, 1), (3, 321, 1),
]
FUNCTION_BLOCK = {1: "coils", 2: "discrete_inputs", 3: "holding_registers", 4: "input_registers"}


def probe_read_spans() -> dict[str, list[tuple[int, int]]]:
    """Address spans of the probe's own requests, per block."""
    spans: dict[str, list[tuple[int, int]]] = {b: [] for b in BLOCK_OF.values()}
    for function_code, start, count in PROBE_READS:
        spans[FUNCTION_BLOCK[function_code]].append((start, start + count - 1))
    return spans


def merge(spans: list[tuple[int, int]]) -> list[list[int]]:
    """Merge overlapping or adjacent inclusive spans."""
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def to_raw(name: str, value) -> dict[int, int]:
    """Convert one decoded library value back to raw register values by address."""
    meta = REGISTERS[name]
    address, scale, datatype = meta[KEY_ADDRESS], meta[KEY_SCALE], meta[KEY_DATATYPE]
    if datatype == TYPE_BIT:
        return {address: 1 if value in (True, "True", 1) else 0}
    if datatype == TYPE_STATUS:
        return {address: STATUS_RAW[value]}
    raw = round(float(value) * scale)
    if datatype in (TYPE_LONG, TYPE_LONG_LE):
        raw &= 0xFFFFFFFF
        hi, lo = raw >> 16, raw & 0xFFFF
        return {address: hi, address + 1: lo} if datatype == TYPE_LONG else {address: lo, address + 1: hi}
    if datatype == TYPE_INT:
        return {address: raw & 0xFFFF}
    if datatype == TYPE_UINT:
        if not 0 <= raw <= 0xFFFF:
            raise ValueError(f"{name}: uint value {value} out of range")
        return {address: raw}
    raise ValueError(f"{name}: unsupported datatype {datatype}")


def apply_supplement(path: Path, values: dict, spans: dict) -> str:
    """Merge raw values from a later, separate read-only capture; returns a provenance note.

    A supplement only fills addresses the main capture did not read; a conflict aborts.
    """
    supplement = json.loads(path.read_text())
    for block, entries in supplement["values"].items():
        for address, raw in entries.items():
            address = int(address)
            if address in values[block]:
                sys.exit(f"error: supplement {path.name} overrides captured {block} {address}")
            values[block][address] = int(raw)
            spans[block].append((address, address))
    listed = ", ".join(f"{b} {', '.join(sorted(e, key=int))}" for b, e in supplement["values"].items())
    return f"supplement {supplement['captured']}: {listed} ({supplement['method']})"


def convert(probe_dir: Path, software: str, controller: str, captured: str, supplements: list[Path] = ()) -> dict:
    summary = json.loads((probe_dir / "summary.json").read_text())
    unknown = set(summary) - SUMMARY_FIELDS
    if unknown:
        sys.exit(f"error: summary.json has unknown fields {sorted(unknown)} — review for identifiers first")
    if summary["kind"] != KIND:
        sys.exit(f"error: capture is for {summary['kind']}, expected {KIND}")
    if summary["firmware"] != software:
        sys.exit(f"error: capture firmware {summary['firmware']} != --software {software}")

    library_values = json.loads((probe_dir / "library_values.json").read_text())
    values: dict[str, dict[int, int]] = {b: {} for b in BLOCK_OF.values()}
    for name, value in library_values.items():
        if value is None:
            continue  # not read (holding 116 is skipped while coil 41 is off)
        block = BLOCK_OF[REGISTERS[name][KEY_REG_TYPE]]
        values[block].update(to_raw(name, value))

    spans = probe_read_spans()
    for label, (block, first) in EXTRAS.items():
        extra = summary["extras"][label]
        if extra.get("error") or extra.get("except"):
            sys.exit(f"error: extra read {label} failed in the capture")
        raws = [int(v) for v in extra["raw"]]
        spans[block].append((first, first + len(raws) - 1))
        for offset, raw in enumerate(raws):
            address = first + offset
            # Extras were read moments after the library pass. Where both exist, keep the
            # library value so the snapshot stays one instant; extras only fill gaps.
            if address in values[block] and values[block][address] != raw:
                print(f"note: {block} {address}: library {values[block][address]} kept, extra read {raw}")
            values[block].setdefault(address, raw)

    # Inputs 57-59: Genesis controller version, confirmed by a raw follow-up read and the
    # display ("Steuerung"). Inside the library's input span, but not a library register.
    for offset, part in enumerate(controller.split(".")):
        values["input_registers"][57 + offset] = int(part)

    supplement_notes = [apply_supplement(path, values, spans) for path in supplements]

    blocks = {}
    for block in BLOCK_OF.values():
        ranges = merge(spans[block])
        outside = [a for a in values[block] if not any(s <= a <= e for s, e in ranges)]
        if outside:
            sys.exit(f"error: {block} values outside answered ranges: {outside}")
        blocks[block] = {
            "ranges": ranges,
            "values": {str(a): values[block][a] for a in sorted(values[block])},
        }

    dynamics = []
    for name, (spread, step) in DYNAMICS.items():
        meta = REGISTERS[name]
        address = meta[KEY_ADDRESS]
        raw = values["input_registers"][address]
        signed = raw - 0x10000 if meta[KEY_DATATYPE] == TYPE_INT and raw >= 0x8000 else raw
        low = signed - spread if meta[KEY_DATATYPE] == TYPE_INT else max(0, signed - spread)
        dynamics.append({
            "block": "input_registers",
            "address": address,
            "name": name.removeprefix("input_"),
            "signed": meta[KEY_DATATYPE] == TYPE_INT,
            "min": low,
            "max": signed + spread,
            "max_step": step,
        })

    return {
        "format": "genesis-wp-profile/1",
        "name": f"stiebel-wpe-i-07.1-plus-{software}",
        "device": "Stiebel Eltron WPE-I 07.1 Plus H 400 (internal WPM-G, Thermia Genesis controller)",
        "model": KIND,
        "firmware": {"software": software, "controller": controller},
        "unit_id": 1,
        "source": {
            "captured": captured,
            "method": (
                "read-only probe (FC1-4) of the real unit with pythermiagenesis17 "
                f"v0.3.0, model {KIND}, max_registers={MAX_REGISTERS}; decoded values "
                "converted back to raw, extra reads stored raw"
            ),
            "notes": [
                "ranges = spans the unit answered without exception; other addresses were never read",
                "addresses inside ranges without a value were read but not kept; the emulator serves 0",
                "holding 116 was not read (library skips it while coil 41 is off)",
                "inputs 57-59 (controller version) from a raw follow-up read and the display",
                "int registers decoded as 0 may have been raw 32767 (library maps 32767 to 0)",
                *supplement_notes,
            ],
        },
        "blocks": blocks,
        "dynamics": dynamics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("probe_dir", type=Path)
    parser.add_argument("--software", required=True, help="controller software, e.g. 6.1.5")
    parser.add_argument("--controller", required=True, help="Genesis controller version, e.g. 17.3.145")
    parser.add_argument("--captured", required=True, help="capture date, YYYY-MM-DD")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--supplement", type=Path, action="append", default=[],
                        help="JSON with raw values from a later read-only capture (repeatable)")
    args = parser.parse_args()

    profile = convert(args.probe_dir, args.software, args.controller, args.captured, args.supplement)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(profile, indent=1) + "\n")
    counts = {b: len(d["values"]) for b, d in profile["blocks"].items()}
    print(f"wrote {args.out}: {counts}")


if __name__ == "__main__":
    main()
