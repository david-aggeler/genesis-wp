"""Package identity, license notices and absence of manufacturer documents (requirement-6)."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
DOCUMENT_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".heic", ".webp")
# sha256 of upstream's MIT permission text (from "Permission is hereby" to the end),
# taken from ulrichwi/pythermiagenesis17 LICENSE on 2026-10-22.
UPSTREAM_PERMISSION_SHA256 = "f9c4c77baa3828004ee54b8a4f2db2e88ed44a6237a493965bf551fac0fcb62d"


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict[str, list[str]]:
    """Build wheel and sdist offline; return their file lists."""
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "--no-build-isolation", "-q", "-w", str(out)],
        check=True,
    )
    subprocess.run(
        [sys.executable, "setup.py", "-q", "sdist", "-d", str(out)],
        cwd=ROOT, check=True, capture_output=True,
    )
    wheel = next(out.glob("*.whl"))
    sdist = next(out.glob("*.tar.gz"))
    with zipfile.ZipFile(wheel) as zf:
        wheel_files = zf.namelist()
        metadata = zf.read(next(n for n in wheel_files if n.endswith(".dist-info/METADATA"))).decode()
    with tarfile.open(sdist) as tf:
        sdist_files = tf.getnames()
    return {"wheel": wheel_files, "sdist": sdist_files, "metadata": metadata.splitlines()}


# DHF-REQ: genesis-wp/requirement-6  DHF-TEST: genesis-wp/ac-14
def test_wheel_is_genesis_wp(built):
    metadata = built["metadata"]
    assert "Name: genesis-wp" in metadata
    assert "License: MIT" in metadata
    top_level = {name.split("/", 1)[0] for name in built["wheel"]}
    packages = {t for t in top_level if not t.endswith(".dist-info")}
    assert packages == {"genesis_wp"}
    assert not any(name.startswith("pythermiagenesis") for name in built["wheel"])


# DHF-REQ: genesis-wp/requirement-6  DHF-TEST: genesis-wp/ac-15
def test_license_keeps_upstream_notices_and_adds_ours():
    text = (ROOT / "LICENSE").read_text()
    lines = text.splitlines()
    for notice in (
        "Copyright (c) 2020 Johan Isacsson",
        "Copyright (c) 2026 Ulrich Wirleitner",
        "Copyright (c) 2026 David Aggeler",
    ):
        assert notice in lines
    permission = text[text.index("Permission is hereby"):]
    assert hashlib.sha256(permission.encode()).hexdigest() == UPSTREAM_PERMISSION_SHA256


# DHF-REQ: genesis-wp/requirement-6  DHF-TEST: genesis-wp/ac-16
def test_no_manufacturer_documents_in_tree():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.split()
    assert not [f for f in tracked if f.lower().endswith(DOCUMENT_SUFFIXES)]


def test_no_manufacturer_documents_in_packages(built):
    for kind in ("wheel", "sdist"):
        assert not [f for f in built[kind] if f.lower().endswith(DOCUMENT_SUFFIXES)], kind


# DHF-REQ: genesis-wp/requirement-2  DHF-TEST: genesis-wp/ac-5
def test_modbus_connection_is_the_only_modbus_dependency(built):
    requires = [line.split(":", 1)[1].strip() for line in built["metadata"] if line.startswith("Requires-Dist:")]
    runtime = [r for r in requires if "extra ==" not in r]
    names = [r.split(";")[0].split("[")[0].split("<")[0].split(">")[0].split("=")[0].strip().lower() for r in runtime]
    assert "modbus-connection" in names
    for direct in ("pymodbus", "tmodbus", "pymodbustcp"):
        assert direct not in names, f"{direct} must come only through modbus-connection's extras"
    spec = next(r for r in runtime if r.lower().startswith("modbus-connection"))
    from packaging.requirements import Requirement
    from packaging.version import Version

    assert Version("4.12.3") in Requirement(spec.split(";")[0]).specifier


# DHF-REQ: genesis-wp/requirement-11  DHF-TEST: genesis-wp/ac-30
def test_root_setup_installs_pdf_text_extraction():
    import re

    script = (ROOT / "scripts" / "setup-as-root.sh").read_text()
    packages = re.search(r"^packages=\((.*)\)$", script, re.M).group(1).split()
    assert "poppler-utils" in packages
