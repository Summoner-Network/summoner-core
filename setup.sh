#!/usr/bin/env bash
# ================
# setup.sh
# ================
#
# This script bootstraps a local development environment for Summoner.
# It is designed to be **sourced** (not executed), so that the virtual
# environment remains active in your current shell session.
#
# Why you must source it
# ----------------------
# If you run `bash setup.sh`, the script runs in a subprocess and the venv
# activation disappears when that subprocess exits.
# If you run `source setup.sh`, the venv activation happens in your current
# shell and remains active after the script finishes.
#
# Usage
# -----
#   source setup.sh [--uv] [--server <version>]
#
# Options
# -------
# --uv
#   Use `uv` instead of `pip` for Python package operations performed by this script.
#
#   Requirements:
#     - `uv` must be installed and available on PATH.
#
#   What changes:
#     - The venv is created with `uv venv`.
#     - Build tools are installed with `uv pip install ...` targeting that venv.
#     - The flag is forwarded to reinstall_python_sdk.sh so it also uses `uv pip`.
#
# --server <version>
#   Select which Rust server prefix is passed to reinstall_python_sdk.sh.
#   This controls which Rust server variant gets (re)installed by the downstream scripts.
#
#   Default:
#     - If omitted, the script uses: rust_server_v1_0_0
#
#   Example:
#     - `--server v1_1_0` becomes: rust_server_v1_1_0
#
# Examples
# --------
# 1) Default (pip, default server):
#    source setup.sh
#
# 2) Use uv (uv must be installed):
#    source setup.sh --uv
#
# 3) Install a different Rust server variant:
#    source setup.sh --server v1_1_0
#
# 4) Combine both:
#    source setup.sh --uv --server v1_1_0
# ================

# set -euo pipefail
set -uo pipefail

# Check if sourced or executed
(return 0 2>/dev/null) && SOURCED=1 || SOURCED=0

if [ "$SOURCED" -ne 1 ]; then
    echo "⚠️  Please source this script instead of executing it:"
    echo "    source $0 [--uv] [--server <version>]"
    echo "Otherwise, the virtual environment will not remain activated after setup."
else
    # Parse args (minimal): support --uv and --server <version>
    USE_UV=false
    SERVER_VERSION=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --uv)
                USE_UV=true
                shift
                ;;
            --server)
                shift
                SERVER_VERSION="${1:-}"
                if [[ -z "$SERVER_VERSION" || "$SERVER_VERSION" == --* ]]; then
                    echo "❌ --server requires a value, e.g. --server v1_1_0"
                    return 1
                fi
                shift
                ;;
            *)
                echo "❌ Unknown argument: $1"
                echo "   Usage: source $0 [--uv] [--server <version>]"
                return 1
                ;;
        esac
    done

    # Default server prefix if not provided
    SERVER_PREFIX="rust_server_v1_0_0"
    if [[ -n "$SERVER_VERSION" ]]; then
        SERVER_PREFIX="rust_server_${SERVER_VERSION}"
    fi

    # Find script directory (relative to $0)
    SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"

    # Create virtual environment
    if [[ "$USE_UV" == "true" ]]; then
        command -v uv >/dev/null 2>&1 || { echo "❌ --uv was set but 'uv' is not on PATH"; return 1; }
        uv venv "$SCRIPT_DIR/venv"
    else
        python3 -m venv "$SCRIPT_DIR/venv"
    fi

    # Activate the virtual environment
    . "$SCRIPT_DIR/venv/bin/activate"

    # Install required packages (build tools)
    if [[ "$USE_UV" == "true" ]]; then
        # ensure uv targets this venv explicitly
        VIRTUAL_ENV="$SCRIPT_DIR/venv" uv pip install --upgrade pip setuptools wheel maturin
    else
        pip install --upgrade pip setuptools wheel maturin
    fi

    # Create the .env file
    cat <<EOF > "$SCRIPT_DIR/.env"
DATABASE_URL=postgres://user:pass@localhost:5432/mydb
SECRET_KEY=supersecret
EOF

    # Reinstall core SDK and Rust crates (forward --uv if requested)
    if [[ "$USE_UV" == "true" ]]; then
        bash "$SCRIPT_DIR/reinstall_python_sdk.sh" "$SERVER_PREFIX" --dev-core --uv
    else
        bash "$SCRIPT_DIR/reinstall_python_sdk.sh" "$SERVER_PREFIX" --dev-core
    fi

    echo "✅ Setup complete."
    echo "   - venv: $SCRIPT_DIR/venv (activated in current shell)"
    echo "   - rust server prefix: $SERVER_PREFIX"
    if [[ "$USE_UV" == "true" ]]; then
        echo "   - python installer: uv"
    else
        echo "   - python installer: pip"
    fi
fi
