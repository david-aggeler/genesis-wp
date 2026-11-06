"""
Python wrapper for getting data from Thermie Genesis heatpump using Modbus TCP
"""

import logging
import math

import datetime
import asyncio
from dataclasses import dataclass

from datetime import timedelta
from operator import itemgetter, attrgetter

from modbus_connection import (
    ModbusConnectionError,
    ModbusError,
    ModbusExceptionError,
    ModbusTcpParams,
    ModbusTimeoutError,
    ModbusUnit,
)

from .const import *
from . import capabilities as _capabilities
from .capabilities import ABSENT, PRESENT, UNKNOWN, Capability
from .groups import register_category, register_concerns, register_group, register_role
from .redaction import RedactionHandle, redact_transport_host

_LOGGER = logging.getLogger(__name__)

# Seconds per Modbus request when the client opens its own connection.
DEFAULT_TIMEOUT = 5.0


class ThermiaException(Exception):
    # DHF-REQ: genesis-wp/requirement-37
    def __init__(self, code=None, *args, **kwargs):
        self.message = ""
        super().__init__(*((code,) if code is not None else ()), *args, **kwargs)
        if code is not None:
            self.code = code
            if isinstance(code, str):
                self.message = self.code
                return


class ThermiaConnectionError(ThermiaException):
    pass


class GenesisReadOnlyError(ThermiaException):
    """A write was attempted on a client constructed without ``read_only=False``."""


class GenesisNotWritableError(ThermiaException):
    """A write was attempted on an input or discrete-input register (requirement-30)."""


class GenesisInvalidWriteError(ThermiaException):
    """A write named an unknown or model-undefined register, or a value it cannot carry exactly (requirement-33)."""


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


# Word range an FC6 write may carry, by holding datatype; any other datatype is not writable.
_WORD_RANGES = {TYPE_INT: (-32768, 32767), TYPE_UINT: (0, 65535)}

# Largest distance of value * scale from an integer still treated as exact (0.29 * 100 = 28.999999999999996).
_SCALE_TOLERANCE = 1e-6


# DHF-REQ: genesis-wp/requirement-38
def _refuse(exc_class, message):
    """Log a write refusal once and construct its unchanged library exception."""
    _LOGGER.warning("%s", message)
    return exc_class(message)


# DHF-REQ: genesis-wp/requirement-33
def _coil_payload(register, value) -> bool:
    """The FC5 bit for a coil write: only 0, 1, True or False, never a truthy coercion."""
    if not isinstance(value, int) or value not in (0, 1):
        raise _refuse(GenesisInvalidWriteError, f"refusing to write {register}: coil value {value!r} is not 0, 1, True or False")
    return bool(value)


# DHF-REQ: genesis-wp/requirement-33
def _holding_payload(register, meta, value, model) -> int:
    """The FC6 word for a holding write: the rounded scaled value, refused unless exact and in range."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _refuse(GenesisInvalidWriteError, f"refusing to write {register}: {value!r} is not a number")
    scaled = value * meta[KEY_SCALE]
    if isinstance(scaled, float) and not math.isfinite(scaled):
        raise _refuse(GenesisInvalidWriteError, f"refusing to write {register}: {value!r} scales to a non-finite value")
    word = round(scaled)
    if abs(scaled - word) > _SCALE_TOLERANCE:
        raise _refuse(GenesisInvalidWriteError,
            f"refusing to write {register}: {value!r} is not exact at scale 1/{meta[KEY_SCALE]}"
        )
    if meta[KEY_DATATYPE] not in _WORD_RANGES:
        raise _refuse(GenesisInvalidWriteError, f"refusing to write {register}: datatype {meta[KEY_DATATYPE]} is not a 16-bit word")
    low, high = _WORD_RANGES[meta[KEY_DATATYPE]]
    if not low <= word <= high:
        raise _refuse(GenesisInvalidWriteError,
            f"refusing to write {register}: {value!r} scales to {word}, outside {low}..{high} for {meta[KEY_DATATYPE]}"
        )
    table = meta.get(KEY_ENUM, {}).get(model)
    if table is not None and word not in table:
        raise _refuse(GenesisInvalidWriteError,
            f"refusing to write {register}: raw value {word} is not declared for model {model}"
        )
    return num_to_bin(word)


def words_to_long(words, big_endian=True):
    """Combine two 16-bit registers into one unsigned 32-bit value."""
    high, low = (words[0], words[1]) if big_endian else (words[1], words[0])
    return (high << 16) | low


def _int16(word):
    """Signed 16-bit value; 32767 reads as 0 (upstream decoding)."""
    if word == 32767:
        return 0
    return word - 65536 if word > 32767 else word


def _last_word(meta):
    """Address of a register's last word: long registers span two."""
    return meta[KEY_ADDRESS] + (1 if meta[KEY_DATATYPE] in (TYPE_LONG, TYPE_LONG_LE) else 0)


# Decoders by datatype for words that need combining or a sign; bits and uint pass through.
_WORD_DECODERS = {
    TYPE_LONG: lambda words, i: words_to_long(words[i : i + 2]),
    TYPE_LONG_LE: lambda words, i: words_to_long(words[i : i + 2], False),
    TYPE_INT: lambda words, i: _int16(words[i]),
}

# Upstream status labels for models without a documented enum (mega, inverter); others read "OFF".
_LEGACY_STATUS_LABELS = {
    1: "Manual Operation", 2: "Defrost", 3: "Hot water", 4: "Heat", 5: "Cool",
    6: "Pool", 7: "Anti legionella", 98: "Standby", 99: "No demand",
}

_WORD_BLOCKS = {REG_COIL: "coils", REG_DISCRETE_INPUT: "discrete_inputs",
                REG_INPUT: "input_registers", REG_HOLDING: "holding_registers"}

_READ_METHODS = {
    REG_COIL: "read_coils",
    REG_DISCRETE_INPUT: "read_discrete_inputs",
    REG_INPUT: "read_input_registers",
    REG_HOLDING: "read_holding_registers",
}


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
                 capability_overrides=None, capabilities_seen=None):
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
        self.last_words: dict = {}  # block -> address -> word from the last update
        self._raw: dict = {}  # raw first word of every register the last read decoded
        self._capability_overrides = _capabilities.validate_overrides(capability_overrides)
        # Seen running in an earlier client, as persisted by the consumer (requirement-13, ac-65).
        self._capabilities_seen: set[str] = _capabilities.validate_seen(capabilities_seen)
        self._unit = unit
        self._connection = None  # our own connection, created lazily; None for an injected unit
        self._redaction: RedactionHandle | None = None  # our host in transport log records
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
    def capabilities_seen(self) -> frozenset[str]:
        """Capabilities seen running so far; persist it and pass it as ``capabilities_seen`` to the next client."""
        return frozenset(self._capabilities_seen)

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

            await self._redact_host()
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

    async def _redact_host(self) -> None:
        """Keep our host and its addresses out of the transport's log records (issue-16)."""
        if self._redaction is None:
            self._redaction = redact_transport_host(str(self._host))
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(self._host, self._port)
        except OSError:
            return  # the connect fails too; its record names only the host, already redacted
        self._redaction.add(*(info[4][0] for info in infos))

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
        if self._redaction is not None:
            self._redaction.remove()
            self._redaction = None

    async def async_set(self, register, value):  # pylint:disable=too-many-branches
        """Write data to heat pump."""
        ret_value = await self._set_data(register, value)
        return ret_value

    async def async_update(
        self, register_types=REG_TYPES, only_registers=None
    ):  # pylint:disable=too-many-branches
        """Update data from heat pump."""
        self.last_words = {block: {} for block in _WORD_BLOCKS.values()}
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
        self._apply_valid_when(raw_data)
        data = {}
        try:
            for i, (name, val) in enumerate(raw_data.items()):
                data[name] = val

            self._update_firmware(data)

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

    def _update_firmware(self, data):
        """Update firmware only when all model-specific version fields are present."""
        if self._kind in GENESIS_17_MODELS:
            attrs = (
                ATTR_INPUT_CONTROL_SOFTWARE_VERSION_MAJOR,
                ATTR_INPUT_CONTROL_SOFTWARE_VERSION_MINOR,
                ATTR_INPUT_CONTROL_SOFTWARE_VERSION_MICRO,
            )
        else:
            attrs = (
                ATTR_INPUT_SOFTWARE_VERSION_MAJOR,
                ATTR_INPUT_SOFTWARE_VERSION_MINOR,
                ATTR_INPUT_SOFTWARE_VERSION_MICRO,
            )
        if all(attr in data for attr in attrs):
            self.firmware = '.'.join(str(data[attr]) for attr in attrs)

    # DHF-REQ: genesis-wp/requirement-29
    def _apply_valid_when(self, values) -> None:
        """None for a register whose declared selector is not in this read or reads another raw value."""
        apply_valid_when(values, self._raw, self._kind)

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

    # DHF-REQ: genesis-wp/requirement-30
    async def _set_data(self, register, value):
        # The only path that issues Modbus writes (FC5/6). Refuse before connecting or sending.
        if self._read_only:
            raise _refuse(GenesisReadOnlyError,
                f"refusing to write {register}: client is read-only (construct with read_only=False to allow writes)"
            )
        meta = REGISTERS.get(register) if isinstance(register, str) else None
        if meta is None or not meta[self._kind]:
            # An unknown name, or an address this model does not document (requirement-33).
            raise _refuse(GenesisInvalidWriteError, f"refusing to write {register!r}: not a register of model {self._kind}")
        # The register type alone decides writability (requirement-30).
        if meta[KEY_REG_TYPE] not in (REG_COIL, REG_HOLDING):
            raise _refuse(GenesisNotWritableError,
                f"refusing to write {register}: a {meta[KEY_REG_TYPE]} register is not writable"
            )
        if meta[KEY_REG_TYPE] == REG_COIL:
            payload = _coil_payload(register, value)
        else:
            payload = _holding_payload(register, meta, value, self._kind)

        self._validate_write_condition(register, meta)
        unit = await self._ensure_unit()

        await asyncio.sleep(self._delay)
        # A concurrent update can invalidate the selector during either await above.
        self._validate_write_condition(register, meta)
        failure = None
        try:
            await self._write(unit, meta, value, payload)
        # ModbusError is the base of every modbus-connection error: exception reply, lost link,
        # timeout, protocol error and desync all map to one host-free error (requirement-30).
        except (ModbusError, OSError) as err:
            failure = err
        except Exception as e:
            _LOGGER.error("Write of %s failed: %s", register, type(e).__name__)
            _LOGGER.debug("Write of %s failed", register, exc_info=True)
            raise
        if failure is not None:
            _LOGGER.error("Write of %s failed: %s", register, type(failure).__name__)
            if isinstance(failure, ModbusExceptionError):
                _LOGGER.debug("Write of %s refused with Modbus exception code %s", register, failure.exception_code)
            # Raised outside the except block, so the chain carries no transport text (requirement-9).
            raise self._connection_failed("Failed to write to", failure) from None
        return value

    # DHF-REQ: genesis-wp/requirement-33
    def _validate_write_condition(self, register, meta):
        """Refuse a conditional write unless the last read proves its selector holds."""
        condition = meta.get(KEY_VALID_WHEN, {}).get(self._kind)
        if condition is None:
            return
        selector, required = condition
        if self._raw.get(selector) != required:
            raise _refuse(GenesisInvalidWriteError,
                f"refusing to write {register}: selector {selector} must read raw value {required}"
            )

    @staticmethod
    async def _write(unit, meta, value, payload) -> None:
        """Send one coil (FC5) or holding (FC6) write of a payload the caller has validated."""
        regtype, address, scale = meta[KEY_REG_TYPE], meta[KEY_ADDRESS], meta[KEY_SCALE]
        _LOGGER.debug(f"Set {regtype} register at {address} value {payload} ({value}) {scale}")
        if regtype == REG_COIL:
            await unit.write_coil(address, payload)
            return
        await unit.write_register(address, payload)

    async def _get_data(self, registers):
        """Retrieve data from heat pump."""
        chunks = self._plan_chunks(registers)
        _LOGGER.debug(
            f"Will make {len(chunks)} requests to read {len(registers)} registers"
        )

        unit = await self._ensure_unit()
        self.unread = set()
        self._enum_raw = {}
        self._raw = {}
        raw_data = {}
        failure = None
        try:
            for chunk in chunks:
                await self._read_chunk(unit, chunk, raw_data)
        except (ModbusConnectionError, ModbusTimeoutError, OSError) as err:
            # A lost link or timeout fails the whole update; only refused chunks are isolated.
            failure = err
        except Exception as e:
            _LOGGER.error("Update failed: %s", type(e).__name__)
            _LOGGER.debug("Update failed", exc_info=True)
        if failure is not None:
            raise self._connection_failed("Lost the connection to", failure) from None

        return raw_data

    def _plan_chunks(self, registers):
        """Split the registers into requests of up to MAX_REGISTERS inside one register block."""
        chunks = []
        chunk = None
        for name in registers:
            meta = REGISTERS[name]
            if name == ATTR_HOLDING_FIXED_SYSTEM_SUPPLY_SET_POINT and self._skip_fixed_supply_set_point(name):
                # A skipped register ends the chunk: no request may span it (issue-7)
                if chunk is not None:
                    chunks.append(chunk)
                    chunk = None
                continue
            if chunk is None or not self._fits(chunk, meta):
                if chunk is not None:
                    chunks.append(chunk)
                chunk = self._new_chunk(name, meta)
            else:
                chunk["slots"][name] = meta[KEY_ADDRESS] - chunk["start"]
                chunk["end"] = _last_word(meta)
        if chunk is not None:
            chunks.append(chunk)
        return chunks

    def _skip_fixed_supply_set_point(self, name):
        """The fixed supply set point errors unless coil 42 is known to be True."""
        enable = ATTR_COIL_ENABLE_FIXED_SYSTEM_SUPPLY_SET_POINT
        if enable not in self.data:
            _LOGGER.debug(
                f"Will not read {name} since we don't know if {enable} is set, include this register in the request to read this"
            )
            return True
        if not self.data.get(enable):
            _LOGGER.debug(
                f"Will not read {name} since {enable} is False which disables this register"
            )
            return True
        return False

    def _fits(self, chunk, meta):
        """Whether a register extends the chunk: same type, within MAX_REGISTERS and the block."""
        address = meta[KEY_ADDRESS]
        return (
            chunk[KEY_REG_TYPE] == meta[KEY_REG_TYPE]
            and address - chunk["start"] < self.MAX_REGISTERS
            and address <= chunk["range_end"]
        )

    def _new_chunk(self, name, meta):
        start = meta[KEY_ADDRESS]
        in_range = [r for r in REGISTER_RANGES[self._kind][meta[KEY_REG_TYPE]] if r[0] <= start <= r[1]]
        return {
            KEY_REG_TYPE: meta[KEY_REG_TYPE],
            "start": start,
            "slots": {name: 0},
            "end": _last_word(meta),
            "range_end": in_range[0][1],
        }

    async def _read_chunk(self, unit, chunk, raw_data):
        """Read one chunk and decode its registers into raw_data; a refused chunk is unread."""
        await asyncio.sleep(self._delay)
        start_address = chunk["start"]
        length = chunk["end"] - chunk["start"] + 1
        regtype = chunk[KEY_REG_TYPE]
        _LOGGER.debug(f"Reading {regtype} {start_address} length {length}")
        read = _READ_METHODS.get(regtype)
        try:
            read_data = await getattr(unit, read)(start_address, length) if read else None
        except ModbusExceptionError as err:
            # The device answered but refused this range: lose only this chunk.
            self._chunk_unread(chunk, length, err)
            return
        if not read_data:
            self._chunk_unread(chunk, length, "empty response")
            return
        # DHF-REQ: genesis-wp/requirement-40
        self.last_words.setdefault(_WORD_BLOCKS[regtype], {}).update(
            {start_address + i: int(word) for i, word in enumerate(read_data[:length])}
        )
        for name, offset in chunk["slots"].items():
            self._raw[name] = read_data[offset]
            raw_data[name] = self._decode(name, read_data, offset)

    def _decode(self, name, words, offset):
        """Decode through the same pure function used for offline recordings."""
        info = REGISTERS[name]
        if info[KEY_DATATYPE] == TYPE_STATUS and info.get(KEY_ENUM, {}).get(self._kind) is not None:
            self._enum_raw[name] = words[offset]
        return decode_register(name, words, offset, self._kind)

    # DHF-REQ: genesis-wp/requirement-35
    @staticmethod
    def _count_value(name, val):
        """A scaled count is an int when integral; otherwise the exact float stays, logged."""
        if val.is_integer():
            return int(val)
        _LOGGER.debug("Count register %s decoded to non-integral %s; kept as float", name, val)
        return val

    def _chunk_unread(self, chunk, length, reason):
        """Record a chunk's registers as unread; their values stay absent, never stale."""
        names = list(chunk["slots"])
        self.unread.update(names)
        _LOGGER.warning(
            "Read of %s %s-%s failed (%s); %d registers unread: %s",
            chunk[KEY_REG_TYPE], chunk["start"], chunk["start"] + length - 1, reason, len(names), ", ".join(names),
        )

# DHF-REQ: genesis-wp/requirement-42
def decode_register(name, words, offset, model, registers=REGISTERS):
    """Decode one register from its chunk's words, driven by its register fields."""
    info = registers[name]
    val = words[offset]
    not_fitted = info.get(KEY_NOT_FITTED, {}).get(model)
    if not_fitted is not None and val == not_fitted:
        return None  # sensor not fitted
    datatype = info[KEY_DATATYPE]
    if datatype == TYPE_STATUS:
        val = _decode_status_value(info, val, model)
    elif datatype in _WORD_DECODERS:
        val = _WORD_DECODERS[datatype](words, offset)
    scale = info[KEY_SCALE]
    if scale != 1:
        val = val / scale
        if info.get(KEY_KIND, {}).get(model) == KIND_COUNT:
            val = ThermiaGenesis._count_value(name, val)
    return val


def _decode_status_value(info, val, model):
    table = info.get(KEY_ENUM, {}).get(model)
    if table is None:
        return _LEGACY_STATUS_LABELS.get(val, "OFF")
    return table.get(val, val)


def apply_valid_when(values, raw_words, model, registers=REGISTERS):
    """Apply the same selector validity contract to live and offline values."""
    for name in values:
        condition = registers[name].get(KEY_VALID_WHEN, {}).get(model)
        if condition is None or values[name] is None:
            continue
        selector, required = condition
        raw = raw_words.get(selector)
        if raw == required:
            continue
        reason = "not in this update" if raw is None else f"reads {raw}, not {required}"
        _LOGGER.debug("%s reported as None: selector %s %s", name, selector, reason)
        values[name] = None
