#!/usr/bin/env bash
set -e

# ─────────────────────────────────────────────────────
# Branch on argument count
# ─────────────────────────────────────────────────────
if [ "$#" -ge 2 ]; then
  WORKDIR="$1"
  CMD="$2"
else
  WORKDIR="$(pwd)"
  CMD="source venv/bin/activate && python ./examples/1_chat_agents/chat_agent.py"
fi

# ─────────────────────────────────────────────────────
# Build the full command
# ─────────────────────────────────────────────────────
FULL_COMMAND="cd \"$WORKDIR\" && $CMD"

# ─────────────────────────────────────────────────────
# Launch in Terminal
# ─────────────────────────────────────────────────────
OS="$(uname)"
if [[ "$OS" == "Darwin" ]]; then
  # macOS Terminal.app
  ESCAPED=$(printf '%s' "$FULL_COMMAND" | sed 's/\\/\\\\/g; s/"/\\"/g')
  osascript <<EOF
tell application "Terminal"
    activate
    do script "$ESCAPED"
end tell
EOF

elif [[ "$OS" == "Linux" ]]; then
  # Linux desktop
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

else
  echo "Unsupported platform: $OS"
  exit 1
fi
