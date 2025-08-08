#!/usr/bin/env bash

set -euo pipefail

# ─────────────────────────────────────────────────────────────
# Resolve paths
# ─────────────────────────────────────────────────────────────
THIS_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$THIS_SCRIPT/.." && pwd)"

# ─────────────────────────────────────────────────────────────
# Parse args: [--dev-core] [<optional-prefix>]
# ─────────────────────────────────────────────────────────────
DEV_CORE=false
PREFIX_FILTER=""

echo "🔍 Raw arguments: $*"

for arg in "$@"; do
  if [[ "$arg" == "--dev-core" ]]; then
    DEV_CORE=true
  elif [[ -z "$PREFIX_FILTER" && "$arg" != --* ]]; then
    PREFIX_FILTER="$arg"
  fi
done

echo "✅ Final values: DEV_CORE=$DEV_CORE, PREFIX_FILTER=$PREFIX_FILTER"

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
  # exit 1
fi

# ─────────────────────────────────────────────────────────────
# Diagnostics
# ─────────────────────────────────────────────────────────────
echo "🐍 Python:   $(which python)"
echo "🔧 Maturin:  $(which maturin || echo 'not found')"

# ─────────────────────────────────────────────────────────────
# Locate Rust SDK directory
# ─────────────────────────────────────────────────────────────
RUST_DIR="$THIS_SCRIPT/summoner/rust"
if [ ! -d "$RUST_DIR" ]; then
  echo "❌ Expected Rust SDK path not found: $RUST_DIR"
  # exit 1
fi

# ─────────────────────────────────────────────────────────────
# Build each matching crate
# ─────────────────────────────────────────────────────────────
FOUND=0
echo "🔍 Searching for Rust crates in: $RUST_DIR (prefix='$PREFIX_FILTER')"

for DIR in "$RUST_DIR"/*/; do
  BASENAME="$(basename "$DIR")"

  # skip if it doesn't match the optional prefix
  if [[ -n "$PREFIX_FILTER" && "$BASENAME" != "$PREFIX_FILTER"* ]]; then
    echo "🚫 Skipping $BASENAME"
    continue
  fi

  if [ -f "$DIR/Cargo.toml" ]; then
    FOUND=1
    echo "🔨 Building $BASENAME with: maturin develop --release"
    ( cd "$DIR" && maturin develop --release )
    echo "✅ Reinstalled crate: $BASENAME"
  else
    echo "⚠️ No Cargo.toml in $BASENAME — skipping"
  fi
done

if [ "$FOUND" -eq 0 ]; then
  echo "⚠️ No matching crates found (prefix='$PREFIX_FILTER')"
fi

echo
if [ "$DEV_CORE" = true ]; then
  echo "ℹ️  Completed with --dev-core (venv at $THIS_SCRIPT/venv)"
else
  echo "ℹ️  Completed with default venv (venv at $ROOT_DIR/venv)"
fi
