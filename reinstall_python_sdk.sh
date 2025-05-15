#!/bin/bash

set -e  # Exit on error


# Activate venv from known relative path (sibling of summoner-src)
VENV_PATH=\"$(cd \"$(dirname \"$0\")/..\" && pwd)/venv\"
if [ -f \"$VENV_PATH/bin/activate\" ]; then
  echo \"✅ Activating venv from $VENV_PATH\"
  . \"$VENV_PATH/bin/activate\"
else
  echo \"❌ Could not find venv at $VENV_PATH\"
  exit 1
fi

# --- Parse optional prefix argument ---
PREFIX_FILTER="$1"  # e.g., "rust_server_sdk_"

# --- Resolve paths ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Reinstall matching Rust crates ---
echo "🔁 Calling reinstall_rust_server.sh with prefix: $PREFIX_FILTER"
bash "$SCRIPT_DIR/reinstall_rust_server.sh" "$PREFIX_FILTER"

# --- Reinstall Python core SDK (editable) ---
cd "$SCRIPT_DIR"

# Check if summoner is already installed and uninstall if so
if pip show summoner > /dev/null 2>&1; then
  echo "🗑️ Uninstalling existing 'summoner' package..."
  pip uninstall -y summoner
else
  echo "ℹ️ 'summoner' not currently installed — skipping uninstall."
fi

# Reinstall as editable
echo "📦 Reinstalling 'summoner' ..."
pip install .

echo "✅ Python core SDK reinstalled with prefix filter: '$PREFIX_FILTER'"
