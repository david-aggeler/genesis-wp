#!/usr/bin/env python
import importlib.util
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

ROOT = Path(__file__).resolve().parent


def _load_version_module():
    """Load genesis_wp/_version.py by path: the build must not import the package (requirement-48)."""
    spec = importlib.util.spec_from_file_location("_genesis_wp_version", ROOT / "genesis_wp" / "_version.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_version = _load_version_module()
VERSION = _version.derive(ROOT)


class BuildPyWithStamp(build_py):
    """Write the derived version into the built package, so installs report it without git."""

    def run(self):
        super().run()
        if not self.editable_mode:
            target = Path(self.build_lib) / "genesis_wp" / _version.STAMP_MODULE
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_version.render_stamp(VERSION), encoding="utf-8")


class SdistWithStamp(sdist):
    """Ship VERSION and the stamp in the sdist, so a build from it needs no git."""

    def make_release_tree(self, base_dir, files):
        super().make_release_tree(base_dir, files)
        base = Path(base_dir)
        (base / _version.VERSION_FILE).write_text(_version.read_core(ROOT) + "\n", encoding="utf-8")
        (base / "genesis_wp" / _version.STAMP_MODULE).write_text(_version.render_stamp(VERSION), encoding="utf-8")


setup(
    name="genesis-wp",
    version=VERSION,
    cmdclass={"build_py": BuildPyWithStamp, "sdist": SdistWithStamp},
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
        "console_scripts": [
            "genesis-wp-emulator=genesis_wp.emulator.cli:main",
            "genesis-wp-climate=genesis_wp.emulator.climate:main",
        ],
    },
    classifiers=[
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python",
        "Operating System :: OS Independent",
    ],
)
