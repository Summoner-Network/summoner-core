#!/usr/bin/env bash

set -euo pipefail

# ─────────────────────────────────────────────────────────────
# Resolve script and project paths
# ─────────────────────────────────────────────────────────────
THIS_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$THIS_SCRIPT/.." && pwd)"
VENV_DIR="$ROOT_DIR/venv"
RUST_DIR="$THIS_SCRIPT/summoner/rust"

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

# Diagnostic: confirm Python & maturin paths
echo "🐍 Python:   $(which python)"
echo "🔧 Maturin:  $(which maturin || echo 'not found')"

# ─────────────────────────────────────────────────────────────
# Parse optional prefix filter
# ─────────────────────────────────────────────────────────────
PREFIX_FILTER="${1:-}"  # Optional CLI arg

# ─────────────────────────────────────────────────────────────
# Validate Rust SDK directory
# ─────────────────────────────────────────────────────────────
if [ ! -d "$RUST_DIR" ]; then
  echo "❌ Expected Rust SDK path not found: $RUST_DIR"
  # exit 1
fi

# ─────────────────────────────────────────────────────────────
# Discover and reinstall matching crates
# ─────────────────────────────────────────────────────────────
FOUND=0
echo "🔍 Searching for Rust crates in: $RUST_DIR"

for DIR in "$RUST_DIR"/*/; do
  BASENAME="$(basename "$DIR")"
  echo "🔎 Found directory: $BASENAME"

  if [[ -n "$PREFIX_FILTER" && "$BASENAME" != $PREFIX_FILTER* ]]; then
    echo "🚫 Skipping: $BASENAME (does not match prefix: $PREFIX_FILTER)"
    continue
  fi

  if [ -f "$DIR/Cargo.toml" ]; then
    FOUND=1
    echo "🔄 Reinstalling crate in: $DIR"

    cd "$DIR"
    echo "🧼 Running cargo clean..."
    cargo clean

    echo "🔨 Rebuilding with: maturin develop --release"
    maturin develop --release

    echo "✅ Reinstalled crate: $BASENAME"
  else
    echo "⚠️ No Cargo.toml found in: $DIR — skipping"
  fi
done

if [ "$FOUND" -eq 0 ]; then
  echo "⚠️ No matching crates found with prefix: '$PREFIX_FILTER'"
fi
