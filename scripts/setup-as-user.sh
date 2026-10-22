#!/usr/bin/env bash
# Create the project virtualenv (.venv/, gitignored) and install genesis-wp with its
# development dependencies. Run as the normal user, after scripts/setup-as-root.sh:
#
#   scripts/setup-as-user.sh            # create or update .venv
#   scripts/setup-as-user.sh --recreate # delete .venv and start fresh
#
# Afterwards: .venv/bin/python -m pytest
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
    echo "error: run as your normal user, not root" >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_ROOT/.venv"
PYTHON="${PYTHON:-python3}"

if [[ "${1:-}" == "--recreate" ]]; then
    rm -rf "$VENV"
fi

# modbus-connection requires Python >= 3.12.
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' || {
    echo "error: $PYTHON is older than 3.12" >&2
    exit 1
}

if [[ ! -x "$VENV/bin/python" ]]; then
    echo "Creating $VENV"
    "$PYTHON" -m venv "$VENV" || {
        echo "error: venv creation failed — run scripts/setup-as-root.sh first (needs python3-venv)" >&2
        exit 1
    }
fi

"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r "$REPO_ROOT/requirements-dev.txt"
"$VENV/bin/python" -m pip install -e "$REPO_ROOT"

echo
echo "Ready. Run the tests with: $VENV/bin/python -m pytest"
