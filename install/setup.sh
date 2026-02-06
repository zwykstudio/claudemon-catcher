#!/bin/bash
#
# Claudemon Setup Script
#
# This script:
# 1. Downloads the latest frontend from GitHub releases
# 2. Sets up the server to run at login (launchd)
# 3. Adds alias to your shell config
#

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Server daemon (HTTP API + dashboard)
SERVER_PLIST_NAME="com.claudemon.server"
SERVER_PLIST_SRC="$SCRIPT_DIR/$SERVER_PLIST_NAME.plist"
SERVER_PLIST_DEST="$HOME/Library/LaunchAgents/$SERVER_PLIST_NAME.plist"

# Engine daemon (watches catches.jsonl → DB → notifications)
ENGINE_PLIST_NAME="com.claudemon.engine"
ENGINE_PLIST_SRC="$SCRIPT_DIR/$ENGINE_PLIST_NAME.plist"
ENGINE_PLIST_DEST="$HOME/Library/LaunchAgents/$ENGINE_PLIST_NAME.plist"

echo "╔════════════════════════════════════════╗"
echo "║          CLAUDEMON SETUP               ║"
echo "╚════════════════════════════════════════╝"
echo ""

# Check prerequisites
echo "→ Checking prerequisites..."

# Check macOS
if [[ "$OSTYPE" != "darwin"* ]]; then
    echo "  ✗ This script only works on macOS"
    exit 1
fi
echo "  ✓ macOS detected"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "  ✗ Python 3 not found. Install from https://python.org"
    exit 1
fi
echo "  ✓ Python 3 found"

# Check Claude CLI
if ! command -v claude &> /dev/null; then
    echo "  ✗ Claude CLI not found."
    echo "    Install: npm install -g @anthropic-ai/claude-cli"
    exit 1
fi
echo "  ✓ Claude CLI found"

# Check GitHub CLI
if ! command -v gh &> /dev/null; then
    echo "  ✗ GitHub CLI not found."
    echo "    Install: brew install gh"
    exit 1
fi

# Check gh authentication
if ! gh auth status &> /dev/null; then
    echo "  ✗ GitHub CLI not authenticated."
    echo "    Run: gh auth login"
    exit 1
fi
echo "  ✓ GitHub CLI authenticated"

echo ""

# Find the correct python3 path (avoid Xcode's sandboxed python)
if [ -x "/usr/local/bin/python3" ]; then
    PYTHON_PATH="/usr/local/bin/python3"
elif [ -x "/opt/homebrew/bin/python3" ]; then
    PYTHON_PATH="/opt/homebrew/bin/python3"
else
    PYTHON_PATH="$(which python3)"
fi

# 1. Install Python dependencies
echo "→ Checking Python dependencies..."
if python3 -c "import cryptography" 2>/dev/null; then
    echo "  ✓ cryptography already installed"
else
    echo "  Installing cryptography..."
    pip3 install --user -q cryptography 2>/dev/null || \
    pip3 install -q cryptography --break-system-packages 2>/dev/null || \
    { echo "  ✗ Failed to install. Run: brew install python-cryptography"; exit 1; }
    echo "  ✓ cryptography installed"
fi
echo ""

# 2. Download frontend
echo "→ Downloading frontend..."
python3 "$BASE_DIR/server/server.py" --update
echo ""

# 3. Install launchd plists
echo "→ Setting up daemons..."
echo "  Using Python: $PYTHON_PATH"

mkdir -p "$HOME/Library/LaunchAgents"

# Server daemon
if launchctl list | grep -q "$SERVER_PLIST_NAME"; then
    echo "  Stopping existing server..."
    launchctl unload "$SERVER_PLIST_DEST" 2>/dev/null || true
fi
sed -e "s|__CLAUDEMON_DIR__|$BASE_DIR|g" -e "s|__PYTHON_PATH__|$PYTHON_PATH|g" "$SERVER_PLIST_SRC" > "$SERVER_PLIST_DEST"
launchctl load "$SERVER_PLIST_DEST"
echo "  ✓ Server daemon installed (HTTP API + dashboard)"

# Engine daemon
if launchctl list | grep -q "$ENGINE_PLIST_NAME"; then
    echo "  Stopping existing engine..."
    launchctl unload "$ENGINE_PLIST_DEST" 2>/dev/null || true
fi
sed -e "s|__CLAUDEMON_DIR__|$BASE_DIR|g" -e "s|__PYTHON_PATH__|$PYTHON_PATH|g" "$ENGINE_PLIST_SRC" > "$ENGINE_PLIST_DEST"
launchctl load "$ENGINE_PLIST_DEST"
echo "  ✓ Engine daemon installed (catches → DB → notifications)"
echo ""

# 4. Shell alias
echo "→ Shell configuration"
echo ""
echo "  Add this alias to your shell config (~/.zshrc or ~/.bashrc):"
echo ""
echo "    alias cc='python3 $BASE_DIR/wrapper.py'"
echo ""
echo "  Or run this command:"
echo ""
echo "    echo \"alias cc='python3 $BASE_DIR/wrapper.py'\" >> ~/.zshrc"
echo ""

# 5. Done
echo "╔════════════════════════════════════════╗"
echo "║            SETUP COMPLETE              ║"
echo "╚════════════════════════════════════════╝"
echo ""
echo "  Dashboard: http://localhost:17712"
echo "  CLI:       cc [args...]"
echo "  Open:      cc --dashboard"
echo ""
echo "  To update frontend: python3 $BASE_DIR/server/server.py --update"
echo "  To stop daemons:"
echo "    launchctl unload ~/Library/LaunchAgents/$SERVER_PLIST_NAME.plist"
echo "    launchctl unload ~/Library/LaunchAgents/$ENGINE_PLIST_NAME.plist"
echo ""
