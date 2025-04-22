#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Create virtual environment
python3 -m venv venv

# Activate the virtual environment
source venv/bin/activate

# Install required packages
pip install -r requirements.txt

# Create the .env file
cat <<EOF > .env
LOG_LEVEL=INFO
ENABLE_CONSOLE_LOG=true
DATABASE_URL=postgres://user:pass@localhost:5432/mydb
SECRET_KEY=supersecret
EOF

# Run reinstall scripts
bash reinstall_rust_server.sh -v 1
bash reinstall_rust_server.sh -v 2
bash reinstall_rust_server.sh -v 2_1
bash reinstall_rust_server.sh -v 2_2
bash reinstall_rust_server.sh -v 2_3

echo "Setup complete. Environment initialized, dependencies installed, and Rust servers reinstalled."

