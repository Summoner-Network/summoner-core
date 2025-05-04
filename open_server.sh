#!/bin/bash

# Define the command you want to run
COMMAND="source venv/bin/activate && python -m templates.myserver --config templates/server_config.json"

# Get the current working directory
WORKDIR="$(pwd)"
FULL_COMMAND="cd \"$WORKDIR\" && $COMMAND"

# Detect the operating system
OS="$(uname)"

if [[ "$OS" == "Darwin" ]]; then
    # Escape for AppleScript (macOS)
    ESCAPED_COMMAND=$(printf '%s' "$FULL_COMMAND" | sed 's/\\/\\\\/g; s/"/\\"/g')

    osascript <<EOF
tell application "Terminal"
    activate
    do script "$ESCAPED_COMMAND"
end tell
EOF

elif [[ "$OS" == "Linux" ]]; then
    if command -v gnome-terminal &> /dev/null; then
        gnome-terminal -- bash -c "$FULL_COMMAND; exec bash"
    elif command -v konsole &> /dev/null; then
        konsole --hold -e bash -c "$FULL_COMMAND"
    elif command -v xterm &> /dev/null; then
        xterm -hold -e "$FULL_COMMAND"
    else
        echo "No supported terminal emulator found"
        exit 1
    fi

elif [[ "$OS" == MINGW* || "$OS" == CYGWIN* || "$OS" == MSYS* ]]; then
    # Windows Git Bash / MSYS2
    powershell.exe -NoExit -Command "cd \"$WORKDIR\"; .\\venv\\Scripts\\activate; python templates\\myserver.py --option rss_2"

else
    echo "Unsupported platform: $OS"
    exit 1
fi
