"""The single source of the genesis-wp version string (requirement-48).

The version is MAJOR.MINOR.PATCH.BUILD:

- MAJOR.MINOR.PATCH is the content of the committed repo-root ``VERSION`` file;
- BUILD is ``git rev-list --count HEAD``, the number of commits since the repository's
  first commit, never reset by a tag.

A build writes the derived value into ``_build_version.py`` next to this module (gitignored),
so an installed wheel or sdist reports the same string without git or a checkout.

:func:`derive` is the build-time derivation (checkout first, then the stamp); setup.py uses it.
:func:`version` is the runtime value (stamp first, then the derivation); package metadata,
``genesis_wp.__version__`` and the command-line ``--version`` flags all report it. A BUILD
one or two behind the newest commit is accepted by design: the count at build time cannot
include later commits.

This module imports nothing from genesis_wp, so setup.py can load it by path.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

#: Name of the version-of-record file at the repository root.
VERSION_FILE = "VERSION"
#: Name of the module a build stamps next to this one.
STAMP_MODULE = "_build_version.py"

_CORE = re.compile(r"^\d+\.\d+\.\d+$")
_FULL = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
_PACKAGE_DIR = Path(__file__).resolve().parent


def read_core(root: Path) -> str:
    """MAJOR.MINOR.PATCH from ``root/VERSION``; raises ValueError for anything else."""
    text = (Path(root) / VERSION_FILE).read_text(encoding="utf-8").strip()
    if not _CORE.match(text):
        raise ValueError(f"{VERSION_FILE} must hold MAJOR.MINOR.PATCH, got {text!r}")
    return text


def _commit_count(root: Path) -> int | None:
    """Commits reachable from HEAD of the checkout at ``root``, or None outside a checkout."""
    if not (Path(root) / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-list", "--count", "HEAD"],
                             check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    count = out.stdout.strip()
    return int(count) if count.isdigit() else None


def stamped(package_dir: Path = _PACKAGE_DIR) -> str | None:
    """The version a build stamped into ``_build_version.py``, or None when there is none."""
    path = Path(package_dir) / STAMP_MODULE
    if not path.is_file():
        return None
    match = re.search(r"""^VERSION\s*=\s*["']([^"']+)["']""", path.read_text(encoding="utf-8"), re.M)
    if match is None or not _FULL.match(match.group(1)):
        raise ValueError(f"{path} carries no MAJOR.MINOR.PATCH.BUILD stamp")
    return match.group(1)


def derive(root: Path, package_dir: Path | None = None) -> str:
    """Build-time version: VERSION plus the commit count in a checkout, else the stamp.

    Outside a checkout and without a stamp, the floor is VERSION + ".0". Without a VERSION
    file either, it raises: there is no placeholder version.
    """
    root = Path(root)
    package_dir = root / "genesis_wp" if package_dir is None else Path(package_dir)
    count = _commit_count(root)
    if count is not None:
        return f"{read_core(root)}.{count}"
    stamp = stamped(package_dir)
    if stamp is not None:
        return stamp
    if (root / VERSION_FILE).is_file():
        return f"{read_core(root)}.0"
    raise RuntimeError("genesis-wp version unknown: no git checkout, no build stamp and no VERSION file")


def render_stamp(value: str) -> str:
    """Source text of ``_build_version.py`` for ``value``."""
    if not _FULL.match(value):
        raise ValueError(f"not a MAJOR.MINOR.PATCH.BUILD version: {value!r}")
    return f'"""Written by the genesis-wp build (requirement-48). Do not edit."""\n\nVERSION = "{value}"\n'


def version() -> str:
    """The version this installation reports: the build stamp, else :func:`derive` of the checkout."""
    stamp = stamped()
    if stamp is not None:
        return stamp
    return derive(_PACKAGE_DIR.parent, _PACKAGE_DIR)
