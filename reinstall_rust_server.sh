#!/bin/bash

set -e  # Exit on error

# --- Parse arguments ---
VERSION=""
while getopts "v:" opt; do
  case ${opt} in
    v )
      VERSION=$OPTARG
      ;;
    \? )
      echo "Usage: $0 -v <version_number>"
      exit 1
      ;;
  esac
done

if [ -z "$VERSION" ]; then
  echo "❌ Error: You must specify a version with -v"
  echo "Usage: $0 -v 1"
  exit 1
fi

# --- Resolve paths ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELAY_DIR="$SCRIPT_DIR/summoner/rust/relay_v$VERSION"

# --- Check if directory exists ---
if [ ! -d "$RELAY_DIR" ]; then
  echo "❌ relay_v$VERSION does not exist at $RELAY_DIR"
  exit 1
fi

echo "🔄 Reinstalling relay_v$VERSION from $RELAY_DIR..."

# --- Clean and reinstall ---
cd "$RELAY_DIR"
echo "🧼 Running cargo clean..."
cargo clean

echo "🔨 Rebuilding with maturin develop --release..."
maturin develop --release

echo "✅ relay_v$VERSION reinstalled and ready to use!"
