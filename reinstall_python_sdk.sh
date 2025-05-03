#!/bin/bash

set -e  # Exit on error

# --- Parse optional prefix argument ---
PREFIX_FILTER="$1"  # e.g., "rust_server_sdk_"

# --- Resolve paths ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUMMONER_DIR="$SCRIPT_DIR/summoner"

# --- Reinstall matching Rust crates ---
echo "🔁 Calling reinstall_rust_server.sh with prefix: $PREFIX_FILTER"
bash "$SCRIPT_DIR/reinstall_rust_server.sh" "$PREFIX_FILTER"

# --- Reinstall Python SDK (editable) ---
cd "$SCRIPT_DIR"

# Check if summoner is already installed and uninstall if so
if pip show summoner > /dev/null 2>&1; then
  echo "🗑️ Uninstalling existing 'summoner' package..."
  pip uninstall -y summoner
else
  echo "ℹ️ 'summoner' not currently installed — skipping uninstall."
fi

# Reinstall as editable
echo "📦 Reinstalling 'summoner' in editable mode..."
pip install -e summoner/

echo "✅ Python SDK reinstalled with prefix filter: '$PREFIX_FILTER'"
