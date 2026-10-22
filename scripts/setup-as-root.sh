#!/usr/bin/env bash
# Install the OS packages genesis-wp development needs. Run once per machine, as root:
#
#   sudo scripts/setup-as-root.sh
#
# Then, as the normal user: scripts/setup-as-user.sh
# Idempotent: re-running only installs what is missing.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "error: run as root (sudo $0)" >&2
    exit 1
fi

# python3-venv: ensurepip, so `python3 -m venv` creates a venv with pip.
# python3-pip:  pip for bootstrapping; project packages go into .venv, never system-wide.
packages=(python3 python3-venv python3-pip)

missing=()
for pkg in "${packages[@]}"; do
    if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
        missing+=("$pkg")
    fi
done

if [[ ${#missing[@]} -eq 0 ]]; then
    echo "All packages already installed: ${packages[*]}"
    exit 0
fi

echo "Installing: ${missing[*]}"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
echo "Done. Next, as your normal user: scripts/setup-as-user.sh"
