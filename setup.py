#!/usr/bin/env python
from setuptools import setup


setup(
    name="genesis-wp",
    version="0.1.0.dev0",
    author="David Aggeler",
    description="Async, read-only-by-default Modbus TCP library and emulator for heat pumps on the Thermia "
        "Genesis controller platform (Stiebel Eltron WPE-I, Thermia Mega, Inverter, Calibra RXT). "
        "Fork of ulrichwi/pythermiagenesis17 and CJNE/pythermiagenesis.",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    include_package_data=True,
    url="https://github.com/david-aggeler/genesis-wp",
    license="MIT",
    packages=["genesis_wp", "genesis_wp.emulator"],
    package_data={"genesis_wp.emulator": ["profiles/*.json", "observations/*.json"]},
    python_requires=">=3.12",
    # Home Assistant's Modbus connection layer (HA 2026.10 ships 4.12.3); pymodbus backend via its extra.
    install_requires=["modbus-connection[pymodbus]>=4.12.3,<5"],
    extras_require={
        # The emulator is a pymodbus server (SimDevice/SimData need >= 3.12; HA 2026.10 ships 3.13.1).
        "emulator": ["pymodbus>=3.13.1"],
    },
    entry_points={
        "console_scripts": ["genesis-wp-emulator=genesis_wp.emulator.cli:main"],
    },
    classifiers=[
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python",
        "Operating System :: OS Independent",
    ],
)
