#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Check if sourced or executed
(return 0 2>/dev/null) && SOURCED=1 || SOURCED=0

if [ "$SOURCED" -ne 1 ]; then
    echo "⚠️  Please source this script instead of executing it:"
    echo "    source $0"
    echo "Otherwise, the virtual environment will not remain activated after setup."
else

    # Find script directory (relative to $0)
    SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"

    # Create virtual environment
    python3 -m venv "$SCRIPT_DIR/venv"

    # Activate the virtual environment
    . "$SCRIPT_DIR/venv/bin/activate"

    # Install required packages
    # pip install -r "$SCRIPT_DIR/requirements.txt"
    pip install --upgrade pip setuptools wheel maturin

    # Create the .env file
    cat <<EOF > "$SCRIPT_DIR/.env"
LOG_LEVEL=INFO
ENABLE_CONSOLE_LOG=true
DATABASE_URL=postgres://user:pass@localhost:5432/mydb
SECRET_KEY=supersecret
EOF

    # Reinstall SDK and Rust crates
    # bash "$SCRIPT_DIR/reinstall_rust_server.sh" rust_server_sdk
    bash "$SCRIPT_DIR/reinstall_python_sdk.sh" rust_server_sdk

    echo "✅ Setup complete. Environment initialized, dependencies installed, and Rust servers reinstalled."
fi
