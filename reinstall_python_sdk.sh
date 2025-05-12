#!/bin/bash

set -e  # Exit on error

# --- Parse optional prefix argument ---
PREFIX_FILTER="$1"  # e.g., "rust_server_sdk_"

# --- Resolve paths ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Reinstall matching Rust crates ---
echo "🔁 Calling reinstall_rust_server.sh with prefix: $PREFIX_FILTER"
bash "$SCRIPT_DIR/reinstall_rust_server.sh" "$PREFIX_FILTER"

# --- Reinstall Python core SDK (editable) ---
cd "$SCRIPT_DIR"

# Check if summoner_core is already installed and uninstall if so
if pip show summoner_core > /dev/null 2>&1; then
  echo "🗑️ Uninstalling existing 'summoner_core' package..."
  pip uninstall -y summoner_core
else
  echo "ℹ️ 'summoner_core' not currently installed — skipping uninstall."
fi

# Reinstall as editable
echo "📦 Reinstalling 'summoner_core' ..."
pip install .

echo "✅ Python core SDK reinstalled with prefix filter: '$PREFIX_FILTER'"
