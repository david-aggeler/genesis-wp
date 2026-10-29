"""Modbus TCP emulator for Genesis-platform heat pumps (genesis-wp-emulator).

Exports load lazily (PEP 562), so a submodule that needs no pymodbus, such as the control
client ``genesis_wp.emulator.control``, imports without the ``emulator`` extra.
"""

from __future__ import annotations

import importlib

_EXPORTS = {
    "BLOCKS": ".profile",
    "COILS": ".profile",
    "DISCRETE_INPUTS": ".profile",
    "HOLDING_REGISTERS": ".profile",
    "INPUT_REGISTERS": ".profile",
    "Profile": ".profile",
    "ProfileError": ".profile",
    "get_profile": ".profile",
    "load_profile": ".profile",
    "packaged_profiles": ".profile",
    "Emulator": ".server",
    "Fault": ".server",
    "Request": ".server",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})
