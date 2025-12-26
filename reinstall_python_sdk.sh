#!/usr/bin/env bash
# ======================
# reinstall_python_sdk.sh
# ======================

# Purpose
# -------
# This script reinstalls the Summoner Python SDK inside an existing virtual environment, and (as part of the workflow)
# also triggers a Rust-side reinstall via `reinstall_rust_server.sh`.

# What it does, step by step
# --------------------------
# 1) Selects which virtual environment to use:
#    - Default:     <repo-root>/venv
#    - With --dev-core: <this-script-dir>/venv

# 2) Activates that virtual environment (it must already exist).

# 3) Reinstalls Rust components by calling:
#    - ./reinstall_rust_server.sh [<optional-prefix>] [--dev-core]

# 4) Reinstalls the Python package named `summoner`:
#    - If `summoner` is already installed, it is uninstalled first.
#    - Then it is installed again from this directory:
#        - Default: installs normally   (pkg install .)
#        - With --dev-core: editable    (pkg install -e .)

# Arguments
# ---------
# Usage:
#   bash reinstall_python_sdk.sh [<optional-prefix>] [--dev-core] [--uv]

# - <optional-prefix>
#   An optional string forwarded to the Rust reinstall script. If you do not need it, omit it.

# - --dev-core
#   Switches the venv location and also switches Python installation mode:
#     - Uses venv at: <this-script-dir>/venv
#     - Installs `summoner` in editable mode (-e), which is useful when you are actively developing the SDK.

# - --uv
#   Uses `uv` as the package manager instead of `pip` for Python install/uninstall/show operations.
#   This is useful in repos that standardize on uv and do not want direct `pip ...` commands in their workflow.

#   Important details:
#     - You must have `uv` installed and available on PATH.
#     - The script still activates a venv; `--uv` only changes how packages are installed inside it.
#     - In `--uv` mode, the script sets VIRTUAL_ENV to the chosen venv path so `uv pip ...` targets the correct environment.

# Environment expectations
# ------------------------
# - A virtual environment must already exist at the expected location:
#     - <repo-root>/venv/bin/activate      (default)
#     - <this-script-dir>/venv/bin/activate (with --dev-core)
#   If it does not exist, create it first (examples below).

# - Default mode requires `pip` to exist inside the venv.
#   If your venv was created in a way that does not include pip, either:
#     - recreate the venv with pip available, or
#     - rerun this script with --uv.

# Examples
# --------
# 1) Typical usage (pip, normal install into <repo-root>/venv):
#    bash reinstall_python_sdk.sh

# 2) With a Rust prefix forwarded to reinstall_rust_server.sh:
#    bash reinstall_python_sdk.sh rust_server_v1_0_0

# 3) Developer workflow (editable install, venv next to this script):
#    bash reinstall_python_sdk.sh --dev-core

# 4) uv workflow (uses uv pip instead of pip):
#    bash reinstall_python_sdk.sh --uv
#    bash reinstall_python_sdk.sh rust_server_v1_0_0 --uv
#    bash reinstall_python_sdk.sh --dev-core --uv

# Creating the venv (if missing)
# ------------------------------
# Default venv:
#   python3 -m venv venv
#   source venv/bin/activate

# uv venv (if your repo standardizes on uv):
#   uv venv venv
#   source venv/bin/activate

# Troubleshooting
# ---------------
# - "Virtualenv not found": create the venv at the expected location (see above).
# - "--uv was set but 'uv' is not on PATH": install uv and ensure it is available in your shell.
# - "'pip' not found in this environment": use --uv or recreate the venv with pip.
# ======================
set -euo pipefail

# ─────────────────────────────────────────────────────────────
# Resolve absolute paths
# ─────────────────────────────────────────────────────────────
THIS_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$THIS_SCRIPT/.." && pwd)"
RUST_SCRIPT="$THIS_SCRIPT/reinstall_rust_server.sh"

# ─────────────────────────────────────────────────────────────
# Parse args: <optional-prefix> [--dev-core] [--uv]
# ─────────────────────────────────────────────────────────────
DEV_CORE=false
USE_UV=false
PREFIX_FILTER=""

echo "🔍 Raw arguments: $*"

for arg in "$@"; do
  if [[ "$arg" == "--dev-core" ]]; then
    DEV_CORE=true
  elif [[ "$arg" == "--uv" ]]; then
    USE_UV=true
  elif [[ -z "$PREFIX_FILTER" && "$arg" != --* ]]; then
    PREFIX_FILTER="$arg"
  fi
done

echo "✅ Final values: DEV_CORE=$DEV_CORE, USE_UV=$USE_UV, PREFIX_FILTER=$PREFIX_FILTER"

# ─────────────────────────────────────────────────────────────
# Select venv location
# ─────────────────────────────────────────────────────────────
if [ "$DEV_CORE" = true ]; then
  VENV_DIR="$THIS_SCRIPT/venv"
else
  VENV_DIR="$ROOT_DIR/venv"
fi

# ─────────────────────────────────────────────────────────────
# Activate virtualenv
# ─────────────────────────────────────────────────────────────
if [ -f "$VENV_DIR/bin/activate" ]; then
  echo "✅ Activating venv from: $VENV_DIR"
  # shellcheck disable=SC1090
  . "$VENV_DIR/bin/activate"
else
  echo "❌ Virtualenv not found at: $VENV_DIR"
  exit 1
fi

# ─────────────────────────────────────────────────────────────
# Package manager shim (pip default, --uv switches to uv pip)
# ─────────────────────────────────────────────────────────────
pkg() {
  if [[ "$USE_UV" == "true" ]]; then
    command -v uv >/dev/null 2>&1 || { echo "❌ --uv was set but 'uv' is not on PATH"; exit 1; }
    # Force uv to target this exact venv, even if activation is flaky.
    VIRTUAL_ENV="$VENV_DIR" uv pip "$@"
  else
    command -v pip >/dev/null 2>&1 || {
      echo "❌ 'pip' not found in this environment. Either recreate the venv with pip, or rerun with --uv."
      exit 1
    }
    pip "$@"
  fi
}

# Diagnostic: show interpreter + tool in use
PY=$(command -v python 2>/dev/null || echo 'not found')
echo "🐍 Using Python: $PY"

if [[ "$USE_UV" == "true" ]]; then
  UV=$(command -v uv 2>/dev/null || echo 'not found')
  UVV=$(uv --version 2>/dev/null || echo 'not found')
  echo "📦 Using uv:     $UV"
  echo "🔧 uv version:   $UVV"
else
  PIP=$(command -v pip 2>/dev/null || echo 'not found')
  PV=$(pip --version 2>/dev/null || echo 'not found')
  echo "📦 Using Pip:    $PIP"
  echo "🔧 Pip version:  $PV"
fi

# ─────────────────────────────────────────────────────────────
# Reinstall Rust crates (optional prefix)
# ─────────────────────────────────────────────────────────────
echo "🔁 Reinstalling Rust crates via: $RUST_SCRIPT"
if [ ! -f "$RUST_SCRIPT" ]; then
  echo "❌ Missing script: $RUST_SCRIPT"
  exit 1
fi

if [ "$DEV_CORE" = true ]; then
  bash "$RUST_SCRIPT" "$PREFIX_FILTER" --dev-core
else
  bash "$RUST_SCRIPT" "$PREFIX_FILTER"
fi

# ─────────────────────────────────────────────────────────────
# Reinstall Python package in editable mode
# ─────────────────────────────────────────────────────────────
echo "🔁 Reinstalling Python package: summoner"

cd "$THIS_SCRIPT"

if pkg show summoner > /dev/null 2>&1; then
  echo "🗑️  Uninstalling existing 'summoner' package..."
  pkg uninstall -y summoner
else
  echo "ℹ️  'summoner' not currently installed — skipping uninstall."
fi

echo "📦 Installing 'summoner'..."
if [ "$DEV_CORE" = true ]; then
  echo "   (dev-core: editable install)"
  pkg install -e .
else
  echo "   (non-dev-core: regular install)"
  pkg install .
fi

echo "✅ Python SDK reinstalled successfully with prefix: '$PREFIX_FILTER'"

if [ "$DEV_CORE" = true ]; then
  echo "   (used --dev-core → venv at $THIS_SCRIPT/venv)"
else
  echo "   (used default → venv at $ROOT_DIR/venv)"
fi

if [[ "$USE_UV" == "true" ]]; then
  echo "   (used --uv → uv pip targeting $VENV_DIR)"
else
  echo "   (used default → pip)"
fi
