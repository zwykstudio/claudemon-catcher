#!/bin/bash
#
# Claudemon Setup Script (macOS + Linux)
#
# This script:
# 1. Checks prerequisites (Python, Claude CLI)
# 2. Configures API key for cloud mode
# 3. Sets up the engine daemon (launchd on macOS, systemd on Linux)
# 4. Cleans up old server daemon if upgrading
# 5. Adds alias to your shell config
#

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "╔════════════════════════════════════════╗"
echo "║          CLAUDEMON SETUP               ║"
echo "╚════════════════════════════════════════╝"
echo ""

# Detect OS
echo "→ Checking prerequisites..."

OS="unknown"
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
    echo "  ✓ macOS detected"
elif [[ "$OSTYPE" == "linux"* ]]; then
    OS="linux"
    echo "  ✓ Linux detected"
else
    echo "  ✗ Unsupported OS: $OSTYPE (macOS and Linux only)"
    echo "    Windows: use WSL or run wrapper.py manually"
    exit 1
fi

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

# Linux: check for notify-send (optional but recommended)
if [[ "$OS" == "linux" ]]; then
    if command -v notify-send &> /dev/null; then
        echo "  ✓ notify-send found (native notifications)"
    else
        echo "  ⚠ notify-send not found (install libnotify for native notifications)"
    fi
fi

echo ""

# Find the correct python3 path
if [[ "$OS" == "macos" ]]; then
    # Avoid Xcode's sandboxed python on macOS
    if [ -x "/usr/local/bin/python3" ]; then
        PYTHON_PATH="/usr/local/bin/python3"
    elif [ -x "/opt/homebrew/bin/python3" ]; then
        PYTHON_PATH="/opt/homebrew/bin/python3"
    else
        PYTHON_PATH="$(which python3)"
    fi
else
    PYTHON_PATH="$(which python3)"
fi

# 1. Install Python dependencies
echo "→ Checking Python dependencies..."
if python3 -c "import cryptography" 2>/dev/null; then
    echo "  ✓ cryptography already installed"
else
    echo "  Installing cryptography..."
    if [[ "$OS" == "macos" ]]; then
        pip3 install --user -q cryptography 2>/dev/null || \
        pip3 install -q cryptography --break-system-packages 2>/dev/null || \
        { echo "  ✗ Failed to install. Run: brew install python-cryptography"; exit 1; }
    else
        pip3 install --user -q cryptography 2>/dev/null || \
        pip3 install -q cryptography --break-system-packages 2>/dev/null || \
        { echo "  ✗ Failed to install. Run: sudo apt install python3-cryptography (or equivalent)"; exit 1; }
    fi
    echo "  ✓ cryptography installed"
fi
echo ""

# 2. Configuration
echo "→ Configuration..."

if [ -n "$CLAUDEMON_API_KEY" ]; then
    echo "  ✓ CLAUDEMON_API_KEY is set (cloud mode)"
elif [ "$CLAUDEMON_MODE" = "local" ]; then
    echo "  ✓ CLAUDEMON_MODE=local (local mode)"
else
    echo "  ⚠ No API key found. Cloud mode requires CLAUDEMON_API_KEY."
    echo ""
    echo "  To get your key:"
    echo "    1. Visit https://claudemon.zwyk-studio.com/dashboard/settings"
    echo "    2. Copy your API key"
    echo "    3. Add to your shell config:"
    echo "       export CLAUDEMON_API_KEY=sk_claudemon_..."
    echo ""
    echo "  For local-only mode (no cloud sync):"
    echo "    export CLAUDEMON_MODE=local"
    echo ""
fi
echo ""

# 3. Cleanup old server daemon (for upgrades from previous versions)
echo "→ Cleaning up old server daemon (if present)..."

if [[ "$OS" == "macos" ]]; then
    OLD_SERVER_PLIST="$HOME/Library/LaunchAgents/com.claudemon.server.plist"
    if [ -f "$OLD_SERVER_PLIST" ]; then
        launchctl unload "$OLD_SERVER_PLIST" 2>/dev/null || true
        rm -f "$OLD_SERVER_PLIST"
        echo "  ✓ Removed old server daemon (launchd)"
    else
        echo "  ✓ No old server daemon found"
    fi
else
    if systemctl --user is-active claudemon-server &>/dev/null || \
       [ -f "$HOME/.config/systemd/user/claudemon-server.service" ]; then
        systemctl --user stop claudemon-server 2>/dev/null || true
        systemctl --user disable claudemon-server 2>/dev/null || true
        rm -f "$HOME/.config/systemd/user/claudemon-server.service"
        systemctl --user daemon-reload 2>/dev/null || true
        echo "  ✓ Removed old server daemon (systemd)"
    else
        echo "  ✓ No old server daemon found"
    fi
fi
echo ""

# 4. Install engine daemon
echo "→ Setting up engine daemon..."
echo "  Using Python: $PYTHON_PATH"

if [[ "$OS" == "macos" ]]; then
    # ── macOS: launchd ──
    ENGINE_PLIST_NAME="com.claudemon.engine"
    ENGINE_PLIST_SRC="$SCRIPT_DIR/$ENGINE_PLIST_NAME.plist"
    ENGINE_PLIST_DEST="$HOME/Library/LaunchAgents/$ENGINE_PLIST_NAME.plist"

    mkdir -p "$HOME/Library/LaunchAgents"

    if launchctl list | grep -q "$ENGINE_PLIST_NAME"; then
        echo "  Stopping existing engine..."
        launchctl unload "$ENGINE_PLIST_DEST" 2>/dev/null || true
    fi
    sed -e "s|__CLAUDEMON_DIR__|$BASE_DIR|g" -e "s|__PYTHON_PATH__|$PYTHON_PATH|g" "$ENGINE_PLIST_SRC" > "$ENGINE_PLIST_DEST"
    launchctl load "$ENGINE_PLIST_DEST"
    echo "  ✓ Engine daemon installed (launchd)"

else
    # ── Linux: systemd user service ──
    SYSTEMD_DIR="$HOME/.config/systemd/user"
    mkdir -p "$SYSTEMD_DIR"

    systemctl --user stop claudemon-engine 2>/dev/null || true
    sed -e "s|__CLAUDEMON_DIR__|$BASE_DIR|g" -e "s|__PYTHON_PATH__|$PYTHON_PATH|g" \
        "$SCRIPT_DIR/claudemon-engine.service" > "$SYSTEMD_DIR/claudemon-engine.service"
    systemctl --user daemon-reload
    systemctl --user enable --now claudemon-engine
    echo "  ✓ Engine daemon installed (systemd)"
fi
echo ""

# 5. Shell alias
SHELL_RC=""
if [ -f "$HOME/.zshrc" ]; then
    SHELL_RC="~/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    SHELL_RC="~/.bashrc"
else
    SHELL_RC="~/.bashrc"
fi

echo "→ Shell configuration"
echo ""
echo "  Add this alias to your shell config ($SHELL_RC):"
echo ""
echo "    alias cc='python3 $BASE_DIR/wrapper.py'"
echo ""
echo "  Or run this command:"
echo ""
echo "    echo \"alias cc='python3 $BASE_DIR/wrapper.py'\" >> $SHELL_RC"
echo ""

# 6. Done
echo "╔════════════════════════════════════════╗"
echo "║            SETUP COMPLETE              ║"
echo "╚════════════════════════════════════════╝"
echo ""
echo "  Dashboard: https://claudemon.zwyk-studio.com/dashboard"
echo "  CLI:       cc [args...]"
echo "  Open:      cc --dashboard"
echo ""
if [[ "$OS" == "macos" ]]; then
    echo "  To stop the engine:"
    echo "    launchctl unload ~/Library/LaunchAgents/com.claudemon.engine.plist"
else
    echo "  To stop the engine:"
    echo "    systemctl --user stop claudemon-engine"
    echo "  To view logs:"
    echo "    journalctl --user -u claudemon-engine -f"
fi
echo ""
