# pythermiagenesis17 

A Python library for Thermia Diplomat Inverter, Mega, and Calibra RXT heat pumps. The Calibra RXT profile also supports the equivalent Stiebel-Eltron WPE-I 07.1.

This library communicates with the device using Modbus TCP.
Set BMC to Modbus TCP on your heat pump to enable communication through this library.

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
thermia = ThermiaGenesis("192.168.1.111", kind="calibra_rxt")
await thermia.async_update()
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

New firmware: capture a read-only probe, then convert it with `scripts/probe_to_profile.py`.

## Development

```sh
sudo scripts/setup-as-root.sh     # once per machine: python3-venv, python3-pip
scripts/setup-as-user.sh          # .venv with dev dependencies pinned to Home Assistant's versions
.venv/bin/python -m pytest
```
