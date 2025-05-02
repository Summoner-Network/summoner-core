#!/bin/bash

# Define color codes
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'  # No color (reset)

# Function to install OpenSSL on macOS
install_openssl_macos() {
    # Check if OpenSSL is already installed via Homebrew
    if ! brew list openssl@1.1 &>/dev/null; then
        echo -e "${GREEN}Installing OpenSSL 1.1 via Homebrew...${NC}"
        brew install openssl@1.1
    else
        echo -e "${GREEN}OpenSSL 1.1 is already installed via Homebrew.${NC}"
    fi
}

# Function to install OpenSSL on Linux (Debian-based systems)
install_openssl_linux() {
    # Ask user whether they want to use sudo for installation
    echo -e "${YELLOW}You are running on Linux. To install OpenSSL and its dependencies via apt, root permissions (sudo) are required.${NC}"
    echo -e "${YELLOW}This will allow the script to install OpenSSL system-wide and modify the system's package list.${NC}"
    
    read -p "Do you want to proceed with sudo privileges (y/n)? " use_sudo
    if [[ "$use_sudo" == "y" ]]; then
        echo -e "${GREEN}Using sudo to install OpenSSL and its dependencies.${NC}"
        sudo apt update
        sudo apt install -y openssl libssl-dev
    else
        echo -e "${YELLOW}You chose not to use sudo. OpenSSL installation will be skipped.${NC}"
        echo -e "${YELLOW}If you wish to install OpenSSL manually, you can run the following commands:${NC}"
        echo ""
        echo -e "${YELLOW}1. Update your package list:${NC}"
        echo -e "${YELLOW}   sudo apt update${NC}"
        echo ""
        echo -e "${YELLOW}2. Install OpenSSL and its dependencies:${NC}"
        echo -e "${YELLOW}   sudo apt install -y openssl libssl-dev${NC}"
    fi
}

# Check system type (macOS or Linux)
if [[ "$OSTYPE" == "darwin"* ]]; then
    install_openssl_macos
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    install_openssl_linux
else
    echo -e "${RED}Unsupported OS. Only macOS and Linux are supported.${NC}"
    exit 1
fi

# Ensure OpenSSL is available
echo -e "${GREEN}Setting up environment variables...${NC}"

# Avoid adding duplicate entries to PATH, LDFLAGS, and CPPFLAGS for Linux and macOS
if [[ "$OSTYPE" == "darwin"* ]]; then
    OPENSSL_PATH="/opt/homebrew/opt/openssl@1.1"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OPENSSL_PATH="$HOME/local/openssl"  # Adjust for user-local installation
fi

# Check the shell and look for the right file to update environment variables
if [[ "$SHELL" == "/bin/zsh" ]]; then
    SHELL_CONFIG_FILE="$HOME/.zshrc"
elif [[ "$SHELL" == "/bin/bash" ]]; then
    SHELL_CONFIG_FILE="$HOME/.bashrc"
else
    echo -e "${RED}Unsupported shell. Only bash and zsh are supported.${NC}"
    exit 1
fi

# Check if the OpenSSL path exists
if [[ ! -d "$OPENSSL_PATH/bin" ]]; then
    echo -e "${RED}Debug: The directory $OPENSSL_PATH/bin does not exist. Please check your OpenSSL installation.${NC}"
    exit 1
fi

if [[ ! -d "$OPENSSL_PATH/lib" ]]; then
    echo -e "${RED}Debug: The directory $OPENSSL_PATH/lib does not exist. Please check your OpenSSL installation.${NC}"
    exit 1
fi

if [[ ! -d "$OPENSSL_PATH/include" ]]; then
    echo -e "${RED}Debug: The directory $OPENSSL_PATH/include does not exist. Please check your OpenSSL installation.${NC}"
    exit 1
fi

# Check if the paths are already present in the appropriate shell configuration file
if ! grep -q "$OPENSSL_PATH/bin" "$SHELL_CONFIG_FILE" 2>/dev/null; then
    export PATH="$OPENSSL_PATH/bin:$PATH"
    echo -e "${GREEN}Added OpenSSL bin directory to PATH.${NC}"
    echo "export PATH=\"$OPENSSL_PATH/bin:\$PATH\"" >> "$SHELL_CONFIG_FILE"
else
    echo -e "${YELLOW}OpenSSL bin directory already exists in PATH. Skipping addition.${NC}"
fi

if ! grep -q "$OPENSSL_PATH/lib" "$SHELL_CONFIG_FILE" 2>/dev/null; then
    export LDFLAGS="-L$OPENSSL_PATH/lib:$LDFLAGS"
    echo -e "${GREEN}Added OpenSSL lib directory to LDFLAGS.${NC}"
    echo "export LDFLAGS=\"-L$OPENSSL_PATH/lib:\$LDFLAGS\"" >> "$SHELL_CONFIG_FILE"
else
    echo -e "${YELLOW}OpenSSL lib directory already exists in LDFLAGS. Skipping addition.${NC}"
fi

if ! grep -q "$OPENSSL_PATH/include" "$SHELL_CONFIG_FILE" 2>/dev/null; then
    export CPPFLAGS="-I$OPENSSL_PATH/include:$CPPFLAGS"
    echo -e "${GREEN}Added OpenSSL include directory to CPPFLAGS.${NC}"
    echo "export CPPFLAGS=\"-I$OPENSSL_PATH/include:\$CPPFLAGS\"" >> "$SHELL_CONFIG_FILE"
else
    echo -e "${YELLOW}OpenSSL include directory already exists in CPPFLAGS. Skipping addition.${NC}"
fi

# Apply the changes to the shell immediately
source "$SHELL_CONFIG_FILE"

# If in a virtual environment, make sure to install `requests`
if [[ -n "$VIRTUAL_ENV" ]]; then
    echo -e "${GREEN}You are in a virtual environment. Installing/updating 'requests'...${NC}"

    # Uninstall requests if already installed
    pip uninstall -y requests

    # Install the latest version of requests
    pip install requests
else
    echo -e "${RED}You are not in a virtual environment. Please activate your venv first.${NC}"
    exit 1
fi

echo -e "${GREEN}OpenSSL and requests installation complete.${NC}"
