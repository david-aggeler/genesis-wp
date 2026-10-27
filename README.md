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

Every register a model enables has a group (the vendor document section, for example `heating_circuit_2`, `brine_circuit`, `power_consumption_control`) and a category (the reference code printed on the register's document row, for example `cooling` for the HC2 cooling settings; registers without a Genesis 17 document row take their group's category). Read them with `register_group(name)` and `register_category(name)`; an alarm also names the group it concerns, `register_concerns(name)`. The tables are in `genesis_wp/groups.py`. `register_role(name)` returns `measurement`, `setting` (holding registers and coils) or `alarm`. For Home Assistant the intended mapping is one sub-device per category, linked to the heat pump's main device, with the role as entity category: measurement → none, setting → config, alarm → diagnostic.

### Plant capabilities

The controller has no "installed modules" register. After each update, `client.capabilities` maps each module the model has registers for (`hot_water`, `heating_circuit_2`, `buffer_tank`, `pool`, `active_cooling`, `passive_cooling`, `hot_gas_recovery`, `room_sensor`, `additional_heater`, `electric_meter`, `smart_grid`, `ksm`, `heating_circuit_3`/`_4`, and `cooling`, which is present when active or passive cooling is present and gates the whole cooling category) to a `Capability` with a `state` (`present`, `absent` or `unknown`), the `evidence` that decided it and the registers it `gates`. Detection uses only values the update read: enable coils, "not fitted" sensors, electric-meter phase voltages (all 0 = no meter), and for `smart_grid` the SG Ready input DI22 or mode register, unknown until one is seen non-zero. `client.capability_state(name)` also answers for modules the model lacks (absent). Override a detection with `capability_overrides={"pool": "present"}` or `client.override_capability(name, state)`. The rules are in `genesis_wp/capabilities.py`.

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

With dynamics on, the emulator also runs a heating model: the HC1 set point (input 18) and the HC2 set point (input 147) follow their seven curve points (holding 6-12, 109-115) over the outdoor temperature (input 13), linearly and clamped to the supply limits; T33, the buffer and T32 follow with a lag, and the mixing valve stays open while the buffer is below the HC2 set point. Curve points written to the emulator take effect on the next step. `--outdoor-temperature -5` (or `outdoor_temperature=-5.0`) holds the outdoor temperature instead of letting it drift.

New firmware: capture a read-only probe, then convert it with `scripts/probe_to_profile.py`.

## Development

```sh
sudo scripts/setup-as-root.sh     # once per machine: python3-venv, python3-pip
scripts/setup-as-user.sh          # .venv with dev dependencies pinned to Home Assistant's versions
.venv/bin/python -m pytest
```
