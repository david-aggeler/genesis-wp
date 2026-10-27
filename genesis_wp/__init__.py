"""
Python wrapper for getting data from Thermie Genesis heatpump using Modbus TCP
"""

import logging

import datetime
import asyncio
from dataclasses import dataclass

from datetime import timedelta
from operator import itemgetter, attrgetter

from modbus_connection import (
    ModbusConnectionError,
    ModbusExceptionError,
    ModbusTcpParams,
    ModbusTimeoutError,
    ModbusUnit,
)

from .const import *
from . import capabilities as _capabilities
from .capabilities import ABSENT, PRESENT, UNKNOWN, Capability
from .groups import register_category, register_concerns, register_group

_LOGGER = logging.getLogger(__name__)

# Seconds per Modbus request when the client opens its own connection.
DEFAULT_TIMEOUT = 5.0


class ThermiaException(Exception):
    def __init__(self, code=None, *args, **kwargs):
        self.message = ""
        super().__init__(*args, **kwargs)
        if code is not None:
            self.code = code
            if isinstance(code, str):
                self.message = self.code
                return


class ThermiaConnectionError(ThermiaException):
    pass


class GenesisReadOnlyError(ThermiaException):
    """A write was attempted on a client constructed without ``read_only=False``."""


@dataclass(frozen=True)
class EnumValue:
    """An enumerated register's value: the raw value and its documented label.

    ``label`` is None when the source documents no label for ``raw`` (never coerced).
    """

    raw: int
    label: str | None

    @property
    def labeled(self) -> bool:
        return self.label is not None


def num_to_bin(value):
    if value > -1:
        return value
    return 65536 + value


def words_to_long(words, big_endian=True):
    """Combine two 16-bit registers into one unsigned 32-bit value."""
    high, low = (words[0], words[1]) if big_endian else (words[1], words[0])
    return (high << 16) | low


class ThermiaGenesis:  # pylint:disable=too-many-instance-attributes
    """Main class to perform modbus requests to heat pump.

    All Modbus I/O goes through Home Assistant's ``modbus-connection``. Either pass
    ``host`` (and ``port``, ``unit_id``) and the client opens and owns its own
    connection, or pass ``unit``, a ``modbus_connection.ModbusUnit`` on a connection the
    caller owns (for example Home Assistant's shared connection).

    The client is read-only unless constructed with ``read_only=False``: every write
    raises :class:`GenesisReadOnlyError` before anything is sent to the device.
    """

    def __init__(self, host=None, port=502, kind="inverter", delay=0.1, max_registers=16, unit_id=1,
                 *, unit: ModbusUnit | None = None, timeout=DEFAULT_TIMEOUT, read_only=True,
                 capability_overrides=None):
        """Initialize."""

        if (host is None) == (unit is None):
            raise ValueError("pass either host or unit, not both and not neither")
        self.data = {}
        normalized_kind = MODEL_ALIASES.get(str(kind).strip().lower(), kind)
        if normalized_kind not in REGISTER_RANGES:
            raise ValueError(f'Unsupported heat pump model: {kind}')
        kind = normalized_kind
        self._read_only = bool(read_only)
        self.unread = set()  # registers the last update could not read (chunk refused by the device)
        # Plant capabilities after the last update (requirement-13), and enum labels (requirement-16).
        self.capabilities: dict[str, Capability] = {}
        self.labels: dict[str, EnumValue] = {}
        self._enum_raw: dict = {}  # raw values of registers decoded to a label during the last read
        self._capability_overrides = _capabilities.validate_overrides(capability_overrides)
        self._capabilities_seen: set[str] = set()
        self._unit = unit
        self._connection = None  # our own connection, created lazily; None for an injected unit
        self._unit_id = unit_id
        self._timeout = timeout
        self.firmware = None
        self.model = MODEL_NAMES[kind]
        self._host = host
        self._port = port
        self._kind = kind
        self._delay = delay
        self.MAX_REGISTERS = max_registers

    @property
    def read_only(self) -> bool:
        """True unless the client was constructed with ``read_only=False``; fixed for its lifetime."""
        return self._read_only

    async def _ensure_unit(self) -> ModbusUnit:
        """Return the unit to talk to, connecting our own connection if we own one."""
        if self._unit is not None and self._connection is None:
            return self._unit  # injected: the caller owns the connection
        if self._connection is None:
            from modbus_connection.pymodbus import ModbusConnection

            self._connection = ModbusConnection(
                ModbusTcpParams(host=self._host, port=self._port), timeout=self._timeout
            )
            self._unit = self._connection.for_unit(self._unit_id)
        if not self._connection.connected:
            # The host is topology: never log it or put it in exception text (requirement-9).
            _LOGGER.info("Opening a Modbus TCP connection to unit %s", self._unit_id)
            failure = None
            try:
                await self._connection.connect()
            except (ModbusConnectionError, ModbusTimeoutError, OSError) as err:
                failure = err
            if failure is not None:
                # Raised outside the except block, so neither __cause__ nor __context__
                # carries the transport error, whose text contains the host.
                raise self._connection_failed("Failed to connect to", failure) from None
        return self._unit

    def _connection_failed(self, what, err) -> ThermiaConnectionError:
        """A host-free connection error; the cause is logged by class name only."""
        _LOGGER.debug("%s unit %s: %s", what, self._unit_id, type(err).__name__)
        return ThermiaConnectionError(f"{what} the Modbus TCP device (unit {self._unit_id})")

    async def close(self):
        """Close the connection this client opened itself; an injected unit is left alone."""
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
            self._unit = None

    async def async_set(self, register, value):  # pylint:disable=too-many-branches
        """Write data to heat pump."""
        ret_value = await self._set_data(register, value)
        return ret_value

    async def async_update(
        self, register_types=REG_TYPES, only_registers=None
    ):  # pylint:disable=too-many-branches
        """Update data from heat pump."""
        await self._ensure_unit()
        if only_registers is None:
            only_registers = [name for name, meta in REGISTERS.items() if meta[self._kind]]
        # The read plan assumes type-and-address order on both paths (issue-7)
        use_registers = sorted(
            only_registers,
            key=lambda x: (REGISTERS[x][KEY_REG_TYPE], REGISTERS[x][KEY_ADDRESS]),
        )

        raw_data = await self._get_data(use_registers)

        if not raw_data:
            self.data = {}
            self.labels = {}
            self.capabilities = {}
            return {}

        # _LOGGER.debug("RAW data: %s", raw_data)
        data = {}
        try:
            for i, (name, val) in enumerate(raw_data.items()):
                data[name] = val

            version_attrs = (ATTR_INPUT_CONTROL_SOFTWARE_VERSION_MAJOR, ATTR_INPUT_CONTROL_SOFTWARE_VERSION_MINOR, ATTR_INPUT_CONTROL_SOFTWARE_VERSION_MICRO) if self._kind in GENESIS_17_MODELS else (ATTR_INPUT_SOFTWARE_VERSION_MAJOR, ATTR_INPUT_SOFTWARE_VERSION_MINOR, ATTR_INPUT_SOFTWARE_VERSION_MICRO)
            if all(attr in data for attr in version_attrs):
                self.firmware = '.'.join(str(data[attr]) for attr in version_attrs)

            _LOGGER.debug("------------- REGISTERS ----------------------")
            for i, (name, val) in enumerate(self.data.items()):
                _LOGGER.debug(f"{REGISTERS[name][KEY_ADDRESS]}\t{val}\t{name}")

        except AttributeError as err:
            _LOGGER.debug("Incomplete data from modbus.")
            _LOGGER.debug(err)
        except KeyError as err:
            _LOGGER.debug("Incomplete data from modbus.")
            _LOGGER.debug(err)
        except TypeError as err:
            _LOGGER.debug("Incomplete data from modbus.")
            _LOGGER.debug(err)
        self.data = data
        self.labels = self._decode_labels(data)
        self.capabilities = _capabilities.evaluate(
            self._kind, data, overrides=self._capability_overrides, seen=self._capabilities_seen
        )
        return data

    def _decode_labels(self, data) -> dict[str, EnumValue]:
        labels = {}
        for name, value in data.items():
            table = REGISTERS[name].get(KEY_ENUM, {}).get(self._kind)
            if table is None or value is None:
                continue
            value = self._enum_raw.get(name, value)  # registers already decoded to their label
            label = table.get(value)
            if label is None:
                _LOGGER.warning("%s: raw value %s has no documented label", name, value)
            labels[name] = EnumValue(value, label)
        return labels

    def capability_state(self, name: str) -> str:
        """State of one capability after the last update: present, absent or unknown.

        A capability the model has no registers for is absent; before any update, or for a
        capability whose evidence was not read, unknown.
        """
        if name not in _capabilities.CAPABILITY_NAMES:
            raise ValueError(f"unknown capability {name!r}")
        if name in self.capabilities:
            return self.capabilities[name].state
        if not _capabilities.gated_registers(_capabilities._RULES[name], self._kind):
            return ABSENT
        return UNKNOWN

    def override_capability(self, name: str, state: str | None) -> None:
        """Override a detected capability (state None removes the override); re-evaluates the last update."""
        overrides = dict(self._capability_overrides)
        if state is None:
            overrides.pop(name, None)
        else:
            overrides[name] = state
        self._capability_overrides = _capabilities.validate_overrides(overrides)
        if self.data:
            self.capabilities = _capabilities.evaluate(
                self._kind, self.data, overrides=self._capability_overrides, seen=self._capabilities_seen
            )

    @property
    def available(self):
        """Return True is data is available."""
        return bool(self.data)

    async def _set_data(self, register, value):
        # The only path that issues Modbus writes (FC5/6). Refuse before connecting or sending.
        if self._read_only:
            raise GenesisReadOnlyError(
                f"refusing to write {register}: client is read-only (construct with read_only=False to allow writes)"
            )
        meta = REGISTERS[register]
        regtype = meta[KEY_REG_TYPE]
        address = meta[KEY_ADDRESS]
        scale = meta[KEY_SCALE]

        unit = await self._ensure_unit()

        await asyncio.sleep(self._delay)
        try:
            if regtype == REG_COIL:
                _LOGGER.debug(
                    f"Set {regtype} register at {address} value {value} ({value})"
                )
                await unit.write_coil(address, bool(value))
            elif regtype == REG_HOLDING:
                converted_value = int(value * scale)
                if meta[KEY_DATATYPE] == TYPE_INT:
                    converted_value = num_to_bin(converted_value)
                _LOGGER.debug(
                    f"Set {regtype} register at {address} value {converted_value} ({value}) {scale}"
                )
                await unit.write_register(address, converted_value)
            else:
                raise "This register can not be changed"
        except Exception as e:
            _LOGGER.error("Write of %s failed: %s", register, type(e).__name__)
            _LOGGER.debug("Write of %s failed", register, exc_info=True)
        return value

    async def _get_data(self, registers):
        """Retreive data from heat pump."""
        raw_data = {}
        # Split into requests that reads up to self.MAX_REGISTERS within REGISTER_RANGES (register blocks) for the requested registers
        first_chunk_address = 0
        current_type = None
        chunks = []
        chunk = None
        for name in registers:
            meta = REGISTERS[name]

            if name == ATTR_HOLDING_FIXED_SYSTEM_SUPPLY_SET_POINT:
                # This will give an errror unless coil 42 is True, so skip if we don't know this or if it's false
                enableAttr = ATTR_COIL_ENABLE_FIXED_SYSTEM_SUPPLY_SET_POINT
                skip = False
                if enableAttr not in raw_data and enableAttr not in self.data:
                    _LOGGER.debug(
                        f"Will not read {name} since we don't know if {ATTR_COIL_ENABLE_FIXED_SYSTEM_SUPPLY_SET_POINT} is set, include this register in the request to read this"
                    )
                    skip = True
                # .get: at planning time raw_data is still empty, and on a later update only self.data has it
                elif (
                    not raw_data.get(ATTR_COIL_ENABLE_FIXED_SYSTEM_SUPPLY_SET_POINT)
                    and not self.data.get(ATTR_COIL_ENABLE_FIXED_SYSTEM_SUPPLY_SET_POINT)
                ):
                    _LOGGER.debug(
                        f"Will not read {name} since {ATTR_COIL_ENABLE_FIXED_SYSTEM_SUPPLY_SET_POINT} is False which disables this register"
                    )
                    skip = True
                if skip:
                    # A skipped register ends the chunk: no request may span it (issue-7)
                    if chunk != None:
                        chunks.append(chunk)
                        chunk = None
                    continue

            reg_address = meta[KEY_ADDRESS]
            if (
                chunk == None  # First iteration
                or chunk[KEY_REG_TYPE] != meta[KEY_REG_TYPE]  # New register type
                or (reg_address - chunk["start"])
                >= self.MAX_REGISTERS  # Exceeds max number of registers per request
                or reg_address > chunk["range_end"]
            ):  # Address belongs to another register block
                if chunk != None:
                    chunks.append(chunk)
                start = meta[KEY_ADDRESS]
                chunk = {
                    KEY_REG_TYPE: meta[KEY_REG_TYPE],
                    "start": start,
                    "slots": {name: 0},
                }

                if (
                    meta[KEY_DATATYPE] == TYPE_LONG
                    or meta[KEY_DATATYPE] == TYPE_LONG_LE
                ):
                    chunk["end"] = start + 1
                else:
                    chunk["end"] = start

                in_range = list(
                    filter(
                        lambda x: x[0] <= start and x[1] >= start,
                        REGISTER_RANGES[self._kind][meta[KEY_REG_TYPE]],
                    )
                )
                chunk["range_end"] = in_range[0][1]

            else:
                chunk["slots"][name] = reg_address - start
                if (
                    meta[KEY_DATATYPE] == TYPE_LONG
                    or meta[KEY_DATATYPE] == TYPE_LONG_LE
                ):
                    chunk["end"] = reg_address + 1
                else:
                    chunk["end"] = reg_address
        if chunk != None:
            chunks.append(chunk)
        _LOGGER.debug(
            f"Will make {len(chunks)} requests to read {len(registers)} registers"
        )

        unit = await self._ensure_unit()
        self.unread = set()
        self._enum_raw = {}
        failure = None
        try:
            for chunk in chunks:
                await asyncio.sleep(self._delay)
                start_address = chunk["start"]
                length = chunk["end"] - chunk["start"] + 1
                regtype = chunk[KEY_REG_TYPE]
                _LOGGER.debug(f"Reading {regtype} {start_address} length {length}")
                read_data = None
                try:
                    if regtype == REG_COIL:
                        read_data = await unit.read_coils(start_address, length)
                    elif regtype == REG_DISCRETE_INPUT:
                        read_data = await unit.read_discrete_inputs(start_address, length)
                    elif regtype == REG_INPUT:
                        read_data = await unit.read_input_registers(start_address, length)
                    elif regtype == REG_HOLDING:
                        read_data = await unit.read_holding_registers(
                            start_address, length
                        )
                except ModbusExceptionError as err:
                    # The device answered but refused this range: lose only this chunk.
                    self._chunk_unread(chunk, length, err)
                    continue
                if read_data:
                    for i, (name, address) in enumerate(chunk["slots"].items()):
                        info = REGISTERS[name]
                        datatype = info[KEY_DATATYPE]
                        scale = info[KEY_SCALE]
                        val = read_data[address]
                        not_fitted = info.get(KEY_NOT_FITTED, {}).get(self._kind)
                        if not_fitted is not None and val == not_fitted:
                            raw_data[name] = None  # sensor not fitted
                            continue
                        if datatype == TYPE_LONG:
                            regs = read_data[address : (address + 2)]
                            val = words_to_long(regs)
                        elif datatype == TYPE_LONG_LE:
                            regs = read_data[address : (address + 2)]
                            val = words_to_long(regs, False)
                        elif datatype == TYPE_INT:
                            if val == 32767:
                                val = 0
                            if val > 32767:
                                val = val - 65536
                        elif datatype == TYPE_STATUS and info.get(KEY_ENUM, {}).get(self._kind) is not None:
                            # Documented demand values (issue-10); an undocumented one stays raw.
                            self._enum_raw[name] = val
                            val = info[KEY_ENUM][self._kind].get(val, val)
                        elif datatype == TYPE_STATUS:
                            status_str = "OFF"
                            if val == 1:
                                status_str = "Manual Operation"
                            elif val == 2:
                                status_str = "Defrost"
                            elif val == 3:
                                status_str = "Hot water"
                            elif val == 4:
                                status_str = "Heat"
                            elif val == 5:
                                status_str = "Cool"
                            elif val == 6:
                                status_str = "Pool"
                            elif val == 7:
                                status_str = "Anti legionella"
                            elif val == 98:
                                status_str = "Standby"
                            elif val == 99:
                                status_str = "No demand"
                            val = status_str

                        if scale != 1:
                            val = val / scale
                        raw_data[name] = val
                else:
                    self._chunk_unread(chunk, length, "empty response")
            # for regtype in register_types:
            #    last_chunk_address = 0
            #    values = []
            #    for chunk in REGISTER_RANGES[self._kind][regtype]:
            #        await asyncio.sleep(self._delay)
            #        start_address = chunk[0]
            #        length = chunk[1] - start_address
            #        #Insert 0 if there is a gap
            #        values.extend([0] * (start_address - last_chunk_address))
            #        _LOGGER.debug(f"Reading {regtype} {start_address} length {length}")
            #        read_data = None
            #        if(regtype == REG_COIL):
            #            read_data = self._client.read_coils(start_address, length)
            #        elif(regtype == REG_DISCRETE_INPUT):
            #            read_data = self._client.read_discrete_inputs(start_address, length)
            #        elif(regtype == REG_INPUT):
            #            read_data = self._client.read_input_registers(start_address, length)
            #        elif(regtype == REG_HOLDING):
            #            read_data = self._client.read_holding_registers(start_address, length)
            #        if read_data:
            #            values.extend(read_data)
            #        else:
            #            if self._client.last_error() > 0:
            #                print(f'error {self._client.last_error()}')
            #                _LOGGER.error(f'error {self._client.last_error()}')
            #            raise Exception(f"Failed to read {regtype} {start_address} length {length}", self._client.last_error())
            #        last_chunk_address = chunk[1]
            #    raw_data[regtype] = values
        except (ModbusConnectionError, ModbusTimeoutError, OSError) as err:
            # A lost link or timeout fails the whole update; only refused chunks are isolated.
            failure = err
        except Exception as e:
            _LOGGER.error("Update failed: %s", type(e).__name__)
            _LOGGER.debug("Update failed", exc_info=True)
        if failure is not None:
            raise self._connection_failed("Lost the connection to", failure) from None

        return raw_data

    def _chunk_unread(self, chunk, length, reason):
        """Record a chunk's registers as unread; their values stay absent, never stale."""
        names = list(chunk["slots"])
        self.unread.update(names)
        _LOGGER.warning(
            "Read of %s %s-%s failed (%s); %d registers unread: %s",
            chunk[KEY_REG_TYPE], chunk["start"], chunk["start"] + length - 1, reason, len(names), ", ".join(names),
        )

