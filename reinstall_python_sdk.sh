#!/usr/bin/env bash

set -euo pipefail

# ─────────────────────────────────────────────────────────────
# Resolve absolute paths
# ─────────────────────────────────────────────────────────────
THIS_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$THIS_SCRIPT/.." && pwd)"
VENV_DIR="$ROOT_DIR/venv"
RUST_SCRIPT="$THIS_SCRIPT/reinstall_rust_server.sh"

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

# Diagnostic: show interpreter in use
echo "🐍 Using Python: $(which python)"
echo "📦 Using Pip:    $(which pip)"
echo "🔧 Pip version:  $(pip --version)"

# ─────────────────────────────────────────────────────────────
# Reinstall Rust crates (optional prefix)
# ─────────────────────────────────────────────────────────────
PREFIX_FILTER="${1:-}"  # Optional CLI argument

echo "🔁 Reinstalling Rust crates via: $RUST_SCRIPT"
if [ ! -f "$RUST_SCRIPT" ]; then
  echo "❌ Missing script: $RUST_SCRIPT"
  exit 1
fi

bash "$RUST_SCRIPT" "$PREFIX_FILTER"

# ─────────────────────────────────────────────────────────────
# Reinstall Python package in editable mode
# ─────────────────────────────────────────────────────────────
echo "🔁 Reinstalling Python package: summoner"

cd "$THIS_SCRIPT"

if pip show summoner > /dev/null 2>&1; then
  echo "🗑️  Uninstalling existing 'summoner' package..."
  pip uninstall -y summoner
else
  echo "ℹ️  'summoner' not currently installed — skipping uninstall."
fi

echo "📦 Installing 'summoner' in editable mode..."
pip install .

echo "✅ Python SDK reinstalled successfully with prefix: '$PREFIX_FILTER'"
