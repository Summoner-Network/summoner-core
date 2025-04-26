#!/bin/bash
set -e

(return 0 2>/dev/null) && SOURCED=1 || SOURCED=0

if [ "$SOURCED" -ne 1 ]; then
    echo "⚠️  Please source this script instead of executing it:"
    echo "    source $0"
    echo "Otherwise, the virtual environment will not remain activated after setup."
else
    # Correct way to find script directory
    SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"

    python3 -m venv "$SCRIPT_DIR/venv"
    . "$SCRIPT_DIR/venv/bin/activate"
    pip install -r "$SCRIPT_DIR/requirements.txt"

    cat <<EOF > "$SCRIPT_DIR/.env"
LOG_LEVEL=INFO
ENABLE_CONSOLE_LOG=true
DATABASE_URL=postgres://user:pass@localhost:5432/mydb
SECRET_KEY=supersecret
EOF

    bash "$SCRIPT_DIR/reinstall_rust_server.sh" rust_server_sdk

    echo "✅ Setup complete. Environment initialized, dependencies installed, and Rust servers reinstalled."
fi
