"""Firmware profiles: the register state of one captured device firmware.

A profile is a JSON file (format ``genesis-wp-profile/1``) with, per Modbus block:

- ``ranges``: inclusive ``[start, end]`` address spans the real unit answered. Requests
  touching any address outside them get Modbus exception 2 (illegal data address).
- ``values``: raw values by address. Addresses inside a range without a value were read
  on the device but not kept; they are served as 0.

plus optional ``dynamics`` entries describing registers that drift when plant dynamics
are enabled. Packaged profiles live next to this module in ``profiles/``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

FORMAT = "genesis-wp-profile/1"

COILS = "coils"
DISCRETE_INPUTS = "discrete_inputs"
HOLDING_REGISTERS = "holding_registers"
INPUT_REGISTERS = "input_registers"
BLOCKS = (COILS, DISCRETE_INPUTS, HOLDING_REGISTERS, INPUT_REGISTERS)
BIT_BLOCKS = (COILS, DISCRETE_INPUTS)


class ProfileError(ValueError):
    """A profile file is malformed or inconsistent."""


@dataclass(frozen=True)
class Block:
    """One Modbus block of a profile."""

    name: str
    ranges: tuple[tuple[int, int], ...]
    values: dict[int, int]

    @property
    def is_bits(self) -> bool:
        return self.name in BIT_BLOCKS

    def implemented(self, address: int) -> bool:
        return any(start <= address <= end for start, end in self.ranges)

    def covers(self, address: int, count: int) -> bool:
        """True if every address of ``address .. address+count-1`` lies inside one range."""
        if count < 1:
            return False
        last = address + count - 1
        return any(start <= address and last <= end for start, end in self.ranges)

    def value(self, address: int) -> int:
        return self.values.get(address, 0)


@dataclass(frozen=True)
class DynamicRegister:
    """A register that drifts within bounds when plant dynamics are on."""

    block: str
    address: int
    name: str
    signed: bool
    min: int
    max: int
    max_step: int


@dataclass(frozen=True)
class Profile:
    """A loaded firmware profile."""

    name: str
    device: str
    firmware: dict[str, str]
    unit_id: int
    source: dict
    blocks: dict[str, Block]
    dynamics: tuple[DynamicRegister, ...] = field(default=())

    @classmethod
    def from_dict(cls, data: dict) -> Profile:
        if data.get("format") != FORMAT:
            raise ProfileError(f"unsupported profile format {data.get('format')!r}, expected {FORMAT!r}")
        try:
            blocks = {}
            for name in BLOCKS:
                raw = data["blocks"][name]
                ranges = tuple((int(s), int(e)) for s, e in raw["ranges"])
                values = {int(a): int(v) for a, v in raw["values"].items()}
                blocks[name] = Block(name, ranges, values)
            dynamics = tuple(DynamicRegister(**entry) for entry in data.get("dynamics", []))
            profile = cls(
                name=data["name"],
                device=data["device"],
                firmware=dict(data["firmware"]),
                unit_id=int(data["unit_id"]),
                source=dict(data["source"]),
                blocks=blocks,
                dynamics=dynamics,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProfileError(f"malformed profile: {exc}") from exc
        profile._validate()
        return profile

    def _validate(self) -> None:
        for block in self.blocks.values():
            for start, end in block.ranges:
                if not 0 <= start <= end <= 0xFFFF:
                    raise ProfileError(f"{block.name}: bad range [{start}, {end}]")
            for address, value in block.values.items():
                if not block.implemented(address):
                    raise ProfileError(f"{block.name}: value at {address} outside every range")
                limit = 1 if block.is_bits else 0xFFFF
                if not 0 <= value <= limit:
                    raise ProfileError(f"{block.name}: value {value} at {address} out of range")
        for dyn in self.dynamics:
            if dyn.block not in self.blocks or self.blocks[dyn.block].is_bits:
                raise ProfileError(f"dynamics {dyn.name}: block must be a register block")
            if not self.blocks[dyn.block].implemented(dyn.address):
                raise ProfileError(f"dynamics {dyn.name}: address {dyn.address} not implemented")
            if not dyn.min <= dyn.max or dyn.max_step < 1:
                raise ProfileError(f"dynamics {dyn.name}: bad bounds or step")


def load_profile(path: str | Path) -> Profile:
    """Load a profile from a JSON file."""
    return Profile.from_dict(json.loads(Path(path).read_text()))


def packaged_profiles() -> list[str]:
    """Names of the profiles shipped with genesis_wp."""
    folder = resources.files(__package__) / "profiles"
    return sorted(p.name.removesuffix(".json") for p in folder.iterdir() if p.name.endswith(".json"))


def get_profile(name_or_path: str | Path) -> Profile:
    """Load a packaged profile by name, or a profile file by path."""
    candidate = Path(name_or_path)
    if candidate.suffix == ".json" or candidate.exists():
        return load_profile(candidate)
    resource = resources.files(__package__) / "profiles" / f"{name_or_path}.json"
    if not resource.is_file():
        raise ProfileError(f"unknown profile {name_or_path!r}; packaged: {', '.join(packaged_profiles())}")
    return Profile.from_dict(json.loads(resource.read_text()))
