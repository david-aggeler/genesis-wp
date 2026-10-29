# genesis-wp

A Python library and device emulator for heat pumps on the Thermia **Genesis** controller platform, over Modbus TCP: Thermia Diplomat Inverter, Mega and Calibra RXT, and the Stiebel Eltron WPE-I 07.1 Plus (internal WPM-G controller). Install it as `genesis-wp` and import it as `genesis_wp`.

> **Work in progress (0.1.0.dev0).** Modbus I/O is async on Home Assistant's [`modbus-connection`](https://github.com/home-assistant-libs/modbus-connection), and the client is **read-only by default**.

### Partial reads

An update reads registers in chunks of up to 16 per request. If the heat pump refuses one chunk (a Modbus exception response), only that chunk's registers are missing from the result; they are listed in `client.unread` and never kept from an earlier update. A lost connection or a timeout still fails the whole update with `ThermiaConnectionError`.

### Sensors that are not fitted

The Stiebel WPE-I reports an unwired sensor with a fixed raw value (20000 at scale 100, 2000 for the room sensor at scale 10), which would otherwise decode as 200.0 °C. For registers where the Stiebel documentation declares this value, the library reports `None` instead. Declarations are per model (`KEY_NOT_FITTED` in `genesis_wp/const.py`); other models keep reporting the number, because Thermia's documents declare no such value.

### Register sources

Every register the WPE-I model reads declares where it comes from, per model (`KEY_SOURCE` in `genesis_wp/const.py`): `model_doc` when Stiebel's WPE-I Modbus document lists it, `platform_doc` when only Thermia's Genesis 17.1 platform document does, and `probe` when only a recorded read-only probe backs it (`KEY_SOURCE_REF` names the capture). A `platform_doc` register may not be supported by the WPE-I, for example the hot-gas recovery and external electricity meter groups. The library still reads and reports it, so a consumer can label it rather than hide it.

### Groups and categories

Every register a model enables has a group (the vendor document section, for example `heating_circuit_2`, `brine_circuit`, `power_consumption_control`) and a category (the reference code printed on the register's document row, for example `cooling` for the HC2 cooling settings; registers without a Genesis 17 document row take their group's category). Read them with `register_group(name)` and `register_category(name)`; an alarm also names the group it concerns, `register_concerns(name)`. The tables are in `genesis_wp/groups.py`. `register_role(name)` returns `measurement`, `setting` (holding registers and coils) or `alarm`. For Home Assistant the intended mapping is one sub-device per category, linked to the heat pump's main device, with the role as entity category: measurement → none, alarm → diagnostic, setting → diagnostic (or none) while it is a read-only sensor, and config only once it is a writable entity (Home Assistant refuses config on sensor and binary_sensor).

### Plant capabilities

The controller has no "installed modules" register. After each update, `client.capabilities` maps each module the model has registers for (`hot_water`, `heating_circuit_2`, `buffer_tank`, `pool`, `active_cooling`, `passive_cooling`, `hot_gas_recovery`, `room_sensor`, `additional_heater`, `electric_meter`, `smart_grid`, `ksm`, `heating_circuit_3`/`_4`, and `cooling`, which is present when active or passive cooling is present and gates the whole cooling category) to a `Capability` with a `state` (installed: `present`, `absent` or `unknown`), `enabled` (switched on: the module's enable coil, `None` where there is none), the `evidence` that decided it and the registers it `gates`. Installed and switched on are separate: a module whose enable coil is off is switched off, not missing. Detection uses only values the update read: "not fitted" sensors (decide installed or missing), an enable coil that is on, a running signal seen earlier (passive cooling), electric-meter phase voltages (all 0 = no meter), and for `smart_grid` the SG Ready input DI22 or mode register, unknown until one is seen non-zero. A switched-off module without such evidence, such as passive cooling in winter, is `unknown`. `client.capability_state(name)` also answers for modules the model lacks (absent). Override a detection with `capability_overrides={"pool": "present"}` or `client.override_capability(name, state)`. The rules are in `genesis_wp/capabilities.py`.

### SG Ready labels

`client.data` keeps raw values. For enumerated registers with documented values, `client.labels[name]` is an `EnumValue(raw, label)`: the SG Ready mode (holding 124: `normal`, `load_up`, `utility_block`, `maximum`) and the smart-grid state (input 82: `utility_block`, `normal`, `comfort`, `maximum`). A value without a documented label has `label=None` and is logged, never mapped to a neighbor.

The demand registers (inputs 1, 55, 56, 62-66) decode to strings such as `Heat` or `No demand`. On the Genesis 17 models (WPE-I, Calibra RXT) they follow the documents' value table, including `Passive cooling` (8) and `OFF` (100), and carry a `client.labels` entry; an undocumented value is reported as its raw number. Legacy models keep the upstream decoding.

### Read-only by default

A client refuses every write (`async_set`) with `GenesisReadOnlyError` before anything is sent to the heat pump. To write, construct it with `read_only=False`; the setting cannot be changed afterwards. Writes go straight to the controller of a real heating plant without any bounds check yet, so only opt in when you mean it.

```python
thermia = ThermiaGenesis(host, kind="stiebel_wpe_i_07_1", read_only=False)
await thermia.async_set("holding_comfort_wheel_setting", 21.0)
```

The heat pump serves Modbus TCP directly. Set BMC to Modbus TCP on your heat pump (Thermia), or connect to the display unit's Ethernet port (Stiebel WPE-I).

### Lineage and license

genesis-wp is a fork of [ulrichwi/pythermiagenesis17](https://github.com/ulrichwi/pythermiagenesis17) (Genesis 17.1 and the Stiebel WPE-I model), which is itself a fork of [CJNE/pythermiagenesis](https://github.com/CJNE/pythermiagenesis). Thanks to both authors. Both upstream libraries install the module `pythermiagenesis`; genesis-wp uses its own module name, so it can be installed next to either of them, for example in a Home Assistant environment. MIT licensed, like its sources (see `LICENSE`).

## documentation
Supports Thermia Modbus protocol up to V17.1

Thermia Modbus TCP documentation: 
- V10:    [Modbus-protocol-for-Genesis-platform-V10.pdf](https://www.tcmadmin.thermia.se/docroot/dokumentbank/Modbus%20protocol%20for%20Genesis%20platform%2010.pdf)
- V17.1:  [Modbus-protocol-for-Genesis-platform-17.1-Calibra.pdf](https://www.geotherma.be/wp-content/uploads/2026/02/Modbus-protocol-for-Genesis-platform-17.1-Calibra.pdf)

Stiebel Eltron Modbus TCP documentation:
- V2025/11: [Stiebel Eltron Modbus_WPE-I_07.1-17.1_Plus_de.pdf](https://www.stiebel-eltron.at/toolbox/content/docs/anleitungen/bedienung/WPE_07.1_17.1_Plus_HW/372257-47978-0071_Modbus_WPE-I_07.1-17.1_Plus_de.pdf)

## notes

1. Be aware that some registers such for example "Compressor operating hours" will require a 32 bit read from two registers if the value is larger than 65535. This library does not handle this automatically, you will have to do this manually. This is explained in the manufacturer documentation.

## Models

- `mega`: Thermia Mega
- `inverter`: Thermia Diplomat Inverter
- `calibra_rxt`: Thermia Calibra RXT
- `stiebel_wpe_i_07_1`: Stiebel-Eltron WPE-I 07.1

Legacy `mega` and `inverter` identifiers remain unchanged.

```python
from genesis_wp import ThermiaGenesis

thermia = ThermiaGenesis("192.168.1.111", kind="calibra_rxt")   # opens its own connection
await thermia.async_update()
await thermia.close()
```

Inside Home Assistant, pass a unit from a shared `modbus-connection` connection instead of a host, so several integrations can use one link to the heat pump:

```python
thermia = ThermiaGenesis(unit=connection.for_unit(1), kind="stiebel_wpe_i_07_1")
```

## Emulator

`genesis_wp.emulator` serves a captured heat pump over Modbus TCP, so you can develop and test without touching a real heating plant. Each firmware profile (`genesis_wp/emulator/profiles/`) holds the address ranges a real unit answered and the raw values read from it, captured by a read-only probe. Addresses outside those ranges get Modbus exception 2, like on the device.

```sh
pip install -e ".[emulator]"
genesis-wp-emulator --list-profiles
genesis-wp-emulator                                    # stiebel-wpe-i-07.1-plus-6.1.5 on 127.0.0.1:5020, unit 1
genesis-wp-emulator -i stiebel-wpe-i-07.1-plus-6.0.6@5021 -i stiebel-wpe-i-07.1-plus-6.1.5@5022
genesis-wp-emulator --dynamics --interval 5 -v         # drifting temperatures and compressor speed, log requests
genesis-wp-emulator --ignore-writes                    # record writes without applying them
```

In tests, use it in-process:

```python
from genesis_wp.emulator import Emulator, get_profile

async with Emulator(get_profile("stiebel-wpe-i-07.1-plus-6.1.5"), port=0) as emu:
    ...                     # connect to emu.host:emu.port, unit 1
    emu.inject_fault("input_registers", 8, 11)   # requests touching input 8-11 now fail
    emu.step()                                   # advance plant dynamics (dynamics=True)
    emu.requests, emu.writes                     # every request / every write received
```

Each profile names the library model of the device it was captured from, so a client can be paired with it without asking: `ThermiaGenesis(emu.host, port=emu.port, kind=emu.profile.model)`. A profile without a known `model` is rejected on load.

To test a state the probes did not capture, pass raw values by `(block, address)`, for example `Emulator(profile, port=0, overrides={("coils", 41): 1, ("holding_registers", 116): 350})`. An address outside the profile's ranges is served on its own; the profile file is not changed.

To present a plant with or without a module, switch library capabilities on or off: `--capability hot_water=on --capability heating_circuit_2=off` on the command line, or `Emulator(profile, port=0, capabilities={"hot_water": True})`. The emulator sets the module's enable coil and fitted or "not fitted" sensor values so the library's capability detection reports exactly the selection. Values the capture does not contain are synthetic and listed in `emu.profile.source["notes"]`. A selection the plant cannot present (an unknown name, a module the model has no registers for, switching off the built-in KSM, or switching off `smart_grid` or `additional_heater`, whose all-zero signals read as unknown) is rejected; the CLI exits with status 2.

With dynamics on, the emulator also runs a heating model: the HC1 set point (input 18) and the HC2 set point (input 147) follow their seven curve points (holding 6-12, 109-115) over the outdoor temperature (input 13), linearly and clamped to the supply limits; T33, the buffer and T32 follow with a lag, and T32 never rises above T33, the supply line the mixer draws from. The compressor is stopped while its speed (input 5) and the condenser pump (input 39) are both 0, for example with `overrides={("input_registers", 5): 0, ("input_registers", 39): 0}`; then T33 holds and the buffer cools toward T32. Heat output (input 157) follows the compressor speed and electric power (input 158) the heat output and the lift (input 9 − input 11), with coefficients fitted to a 20 h series of a real WPE-I; both are 0 while stopped. The mixing-valve register (input 43) is the valve's control signal and is served as captured. Curve points written to the emulator take effect on the next step. A sensor served as not fitted, for example by a capability switched off, is never ticked. The emulator does not start or stop the compressor by itself. `--outdoor-temperature -5` (or `outdoor_temperature=-5.0`) holds the outdoor temperature instead of letting it drift.

The heating model also covers HC2's passive cooling, for testing a heat/cool switch against the emulator only (the real plant is never written). With coil 33 on and holding 298 at 1 (cool), or at 2 (auto) with the outdoor temperature at or above holding 303, HC2 cools toward holding 302: input 1 reports passive cooling and discrete input 245 turns on. A write-enabled test client changes these registers, and the emulator applies them on its next step.

Room set points shift the heating curves as the plant does, with or without dynamics (formal_review-3). Writing HC1's room set point (holding 5) moves holding 6-12 by 2 K per K. HC2's room set point has no register on the WPE-I, so the emulator offers it only through its API (`emu.set_room_set_point_hc2(22.5)`, `emu.room_set_point_hc2`) and `--room-set-point-hc2 22.5`; it moves holding 109-115 by 1 K per K. Both keep the plant's reference rule: the curve moves by (new − reference) rounded to whole kelvin, halves to even, and the reference follows only when the curve moved, so half steps add up. The rules are observations of firmware 6.1.5 in `genesis_wp/emulator/observations/`; a profile without them shifts nothing and rejects the HC2 call.

A climate profile is the typical outdoor temperature of a location: for each month (or week) of the year, the mean at every local hour of the day over the last 5 full years, from the hourly archive of the [Open-Meteo historical weather API](https://open-meteo.com) (ERA5, CC BY 4.0), without interpolation. Build one once; this is the only step that uses the network:

```sh
genesis-wp-climate build --latitude 47.37 --longitude 8.54 -o climate.json            # 12 x 24 values
genesis-wp-climate build --latitude 47.37 --longitude 8.54 --bucket week -o climate.json   # 52 x 24
```

The file carries the coordinates, so keep the one for your home out of version control; nothing logs them.

Play it back with dynamics on: the emulator keeps a simulated clock that advances by the playback speed (simulated seconds per real second) and serves the profile value of the simulated month (or week) and hour as the outdoor temperature (input 13), held for the whole hour. One model step is one simulated minute, so the plant responds the same at any speed. A held outdoor temperature still wins.

```sh
genesis-wp-emulator --dynamics --interval 1 --climate climate.json --start 2026-01-15T06:00 --speed 60   # 1 h per minute
```

In Python: `Emulator(..., dynamics=True, climate=ClimateProfile.load("climate.json"), start_time=..., playback_speed=60)`, then `emu.simulated_time`, `emu.set_playback_speed(3600)`, and `emu.advance(seconds)` to jump ahead.

### Control channel

The emulator's Modbus surface is the plant's, so emulator-only controls get their own TCP port: the control channel. Plant values stay on Modbus. That includes set points the plant has registers for, such as HC1's room set point in holding 5. The channel has no operation for a value the real unit has no register for, such as HC2's room set point.

```sh
genesis-wp-emulator --dynamics --interval 1 --climate climate.json --control-port 5520
genesis-wp-emulator ctl 127.0.0.1:5520 identify
genesis-wp-emulator ctl 127.0.0.1:5520 speed 3600          # 1 h per second
genesis-wp-emulator ctl 127.0.0.1:5520 pause               # resume
genesis-wp-emulator ctl 127.0.0.1:5520 time 2026-07-15T14:00
genesis-wp-emulator ctl 127.0.0.1:5520 hold -5             # release
genesis-wp-emulator ctl 127.0.0.1:5520 state
```

The client, `genesis_wp.emulator.control.EmulatorControl`, is plain asyncio and imports without pymodbus, so tests and integrations use it without the `emulator` extra:

```python
from genesis_wp.emulator.control import EmulatorControl, NotAnEmulator

async with EmulatorControl(host, control_port) as control:
    identity = await control.identify()   # raises NotAnEmulator for anything but this emulator
    await control.set_playback_speed(60)
    state = await control.state()         # simulated_time, playback_speed, paused, outdoor_temperature, ...
```

`identify()` is the check against a wrong host: a real heat pump has no control endpoint, so it raises `NotAnEmulator`. Invalid values are rejected with `ControlError(code="invalid_value")` and change nothing. Clock operations without a climate profile, and outdoor operations without dynamics, raise `ControlError(code="unavailable")`. In-process, `Emulator(..., control_port=0)` binds a free port and reports it in `emu.control_port` after `start()`.

The wire format is one JSON object per line, protocol version 1, described in `genesis_wp/emulator/control.py`. The port binds the emulator's host, loopback by default, and has no authentication.

New firmware: capture a read-only probe, then convert it with `scripts/probe_to_profile.py`.

## Development

```sh
sudo scripts/setup-as-root.sh     # once per machine: python3-venv, python3-pip
scripts/setup-as-user.sh          # .venv with dev dependencies pinned to Home Assistant's versions
.venv/bin/python -m pytest
```

The suite runs against the emulator and opens no connection beyond loopback. Every test
module declares its class with a marker:

| Marker | Meaning |
|---|---|
| `plant_read` | Invariants that hold on any healthy plant (decoding, types, plausible temperatures, documented enum values, the supply set point following the clamped heat curve) |
| `fixture` | Compares against captured profile values or uses an emulator scenario |
| `writes` | Writes registers |
| `dynamics` | Needs emulator dynamics or the simulated clock |
| `emulator` | Tests the emulator itself |
| `emulator_control` | Uses the emulator control channel |
| `unit` | Needs no device |

### Running the tests against the real heat pump

The plant read tests also run, read-only, against the real heat pump. A plant read test is
trusted once it passes there; then the emulator has to pass it too. A live run is an
owner-approved act every time, never automated:

```sh
GENESIS_WP_TEST_TARGET=live GENESIS_WP_LIVE_HOST=<host> .venv/bin/python -m pytest
```

`GENESIS_WP_LIVE_PORT` defaults to 502. On the live target:

- Only `plant_read` tests run. Every other test is skipped and the reason names its class.
- The plant is read once per run, one full update of about 36 requests.
- Reads go through a unit that has no write methods, under the library's own read-only
  client, so a write fails before a connection is made.
- The network guard allows exactly the host's address and port. Log records have the host
  replaced, and the report names the target only as `live`.

A failing plant read test is a finding. Either the test asserts something the plant does not
guarantee, or the profile or the library is wrong. File it as an issue.
