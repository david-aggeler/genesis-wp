"""Modbus TCP emulator for Genesis-platform heat pumps (genesis-wp-emulator)."""

from .profile import (
    BLOCKS,
    COILS,
    DISCRETE_INPUTS,
    HOLDING_REGISTERS,
    INPUT_REGISTERS,
    Profile,
    ProfileError,
    get_profile,
    load_profile,
    packaged_profiles,
)
from .server import Emulator, Fault, Request

__all__ = [
    "BLOCKS",
    "COILS",
    "DISCRETE_INPUTS",
    "HOLDING_REGISTERS",
    "INPUT_REGISTERS",
    "Emulator",
    "Fault",
    "Profile",
    "ProfileError",
    "Request",
    "get_profile",
    "load_profile",
    "packaged_profiles",
]
