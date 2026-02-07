#!/bin/bash
#
# Claudemon Setup Script (macOS + Linux)
#
# This script:
# 1. Checks prerequisites (Python, Claude CLI)
# 2. Configures API key or local mode
# 3. Injects env vars into daemon + shell config
# 4. Sets up the engine daemon (launchd on macOS, systemd on Linux)
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

# 2. Configuration — determine mode and collect env vars
#    We build an associative list of env vars to inject everywhere.
echo "→ Configuration..."

CLAUDEMON_ENV_MODE=""   # "cloud" or "local"

if [ -n "$CLAUDEMON_API_KEY" ]; then
    CLAUDEMON_ENV_MODE="cloud"
    echo "  ✓ CLAUDEMON_API_KEY detected (cloud mode)"
elif [ "$CLAUDEMON_MODE" = "local" ]; then
    CLAUDEMON_ENV_MODE="local"
    echo "  ✓ CLAUDEMON_MODE=local (local mode)"
else
    # Interactive: ask the user
    echo ""
    echo "  No configuration found. Choose a mode:"
    echo ""
    echo "    1) Cloud (default) — sync to https://claudemon.zwyk-studio.com"
    echo "    2) Local — SQLite only, no cloud sync"
    echo ""
    read -rp "  Choice [1]: " MODE_CHOICE
    MODE_CHOICE="${MODE_CHOICE:-1}"

    if [ "$MODE_CHOICE" = "2" ]; then
        CLAUDEMON_ENV_MODE="local"
        export CLAUDEMON_MODE="local"
        echo "  ✓ Local mode selected"
    else
        echo ""
        echo "  Paste your API key (get one at https://claudemon.zwyk-studio.com/dashboard/settings):"
        read -rp "  API key: " INPUT_KEY
        INPUT_KEY="$(echo "$INPUT_KEY" | xargs)"  # trim whitespace
        if [ -z "$INPUT_KEY" ]; then
            echo "  ✗ No key provided. Re-run setup when you have your key."
            exit 1
        fi
        if [[ "$INPUT_KEY" != sk_claudemon_* ]]; then
            echo "  ✗ Invalid key. It should start with sk_claudemon_"
            exit 1
        fi
        export CLAUDEMON_API_KEY="$INPUT_KEY"
        CLAUDEMON_ENV_MODE="cloud"
        echo "  ✓ API key set (cloud mode)"
    fi
fi

# Collect env vars: NAME=VALUE pairs (one per line)
ENV_VARS=""
SHELL_EXPORTS=""

if [ "$CLAUDEMON_ENV_MODE" = "cloud" ]; then
    ENV_VARS="CLAUDEMON_API_KEY=$CLAUDEMON_API_KEY"
    SHELL_EXPORTS="export CLAUDEMON_API_KEY='$CLAUDEMON_API_KEY'"
else
    ENV_VARS="CLAUDEMON_MODE=local"
    SHELL_EXPORTS="export CLAUDEMON_MODE=local"
fi

# Include CLAUDEMON_CLOUD_URL if set (custom API endpoint)
if [ -n "$CLAUDEMON_CLOUD_URL" ]; then
    ENV_VARS="$ENV_VARS
CLAUDEMON_CLOUD_URL=$CLAUDEMON_CLOUD_URL"
    SHELL_EXPORTS="$SHELL_EXPORTS
export CLAUDEMON_CLOUD_URL='$CLAUDEMON_CLOUD_URL'"
    echo "  ✓ Custom cloud URL: $CLAUDEMON_CLOUD_URL"
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

# 4. Install engine daemon (with env vars injected)
echo "→ Setting up engine daemon..."
echo "  Using Python: $PYTHON_PATH"

if [[ "$OS" == "macos" ]]; then
    # ── macOS: launchd + PlistBuddy for env vars ──
    ENGINE_PLIST_NAME="com.claudemon.engine"
    ENGINE_PLIST_SRC="$SCRIPT_DIR/$ENGINE_PLIST_NAME.plist"
    ENGINE_PLIST_DEST="$HOME/Library/LaunchAgents/$ENGINE_PLIST_NAME.plist"

    mkdir -p "$HOME/Library/LaunchAgents"

    if launchctl list | grep -q "$ENGINE_PLIST_NAME"; then
        echo "  Stopping existing engine..."
        launchctl unload "$ENGINE_PLIST_DEST" 2>/dev/null || true
    fi

    # Copy template with basic substitutions
    sed -e "s|__CLAUDEMON_DIR__|$BASE_DIR|g" \
        -e "s|__PYTHON_PATH__|$PYTHON_PATH|g" \
        "$ENGINE_PLIST_SRC" > "$ENGINE_PLIST_DEST"

    # Inject env vars with PlistBuddy (supports any number of vars)
    PB=/usr/libexec/PlistBuddy
    $PB -c "Add :EnvironmentVariables dict" "$ENGINE_PLIST_DEST" 2>/dev/null || true
    echo "$ENV_VARS" | while IFS='=' read -r key value; do
        $PB -c "Delete :EnvironmentVariables:$key" "$ENGINE_PLIST_DEST" 2>/dev/null || true
        $PB -c "Add :EnvironmentVariables:$key string $value" "$ENGINE_PLIST_DEST"
    done

    launchctl load "$ENGINE_PLIST_DEST"
    echo "  ✓ Engine daemon installed (launchd)"

else
    # ── Linux: systemd user service ──
    SYSTEMD_DIR="$HOME/.config/systemd/user"
    mkdir -p "$SYSTEMD_DIR"
    SERVICE_DEST="$SYSTEMD_DIR/claudemon-engine.service"

    systemctl --user stop claudemon-engine 2>/dev/null || true

    # Copy template with basic substitutions
    sed -e "s|__CLAUDEMON_DIR__|$BASE_DIR|g" \
        -e "s|__PYTHON_PATH__|$PYTHON_PATH|g" \
        "$SCRIPT_DIR/claudemon-engine.service" > "$SERVICE_DEST"

    # Inject env vars (one Environment= line per var, after WorkingDirectory)
    echo "$ENV_VARS" | while IFS='=' read -r key value; do
        sed -i "/^WorkingDirectory=/a Environment=$key=$value" "$SERVICE_DEST"
    done

    systemctl --user daemon-reload
    systemctl --user enable --now claudemon-engine
    echo "  ✓ Engine daemon installed (systemd)"
fi
echo ""

# 5. Shell configuration (alias + env vars)
SHELL_RC=""
SHELL_RC_PATH=""
if [ -f "$HOME/.zshrc" ]; then
    SHELL_RC="~/.zshrc"
    SHELL_RC_PATH="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    SHELL_RC="~/.bashrc"
    SHELL_RC_PATH="$HOME/.bashrc"
else
    SHELL_RC="~/.bashrc"
    SHELL_RC_PATH="$HOME/.bashrc"
fi

ALIAS_LINE="alias cc='python3 $BASE_DIR/wrapper.py'"

# Check what's already in shell config
NEED_ALIAS=true
NEED_ENV=true
if [ -f "$SHELL_RC_PATH" ]; then
    grep -qF "alias cc=" "$SHELL_RC_PATH" 2>/dev/null && NEED_ALIAS=false
    if [ "$CLAUDEMON_ENV_MODE" = "cloud" ]; then
        grep -qF "CLAUDEMON_API_KEY" "$SHELL_RC_PATH" 2>/dev/null && NEED_ENV=false
    else
        grep -qF "CLAUDEMON_MODE" "$SHELL_RC_PATH" 2>/dev/null && NEED_ENV=false
    fi
fi

LINES_TO_ADD=""
if $NEED_ENV; then
    LINES_TO_ADD="$SHELL_EXPORTS"
fi
if $NEED_ALIAS; then
    if [ -n "$LINES_TO_ADD" ]; then
        LINES_TO_ADD="$LINES_TO_ADD
$ALIAS_LINE"
    else
        LINES_TO_ADD="$ALIAS_LINE"
    fi
fi

echo "→ Shell configuration ($SHELL_RC)"

if [ -z "$LINES_TO_ADD" ]; then
    echo "  ✓ Already configured"
else
    echo ""
    echo "  Adding to $SHELL_RC:"
    echo ""
    echo "$LINES_TO_ADD" | while IFS= read -r line; do
        echo "    $line"
    done
    echo ""
    read -rp "  OK? [Y/n] " CONFIRM
    CONFIRM="${CONFIRM:-Y}"
    if [[ "$CONFIRM" =~ ^[Yy] ]]; then
        echo "" >> "$SHELL_RC_PATH"
        echo "# Claudemon" >> "$SHELL_RC_PATH"
        echo "$LINES_TO_ADD" >> "$SHELL_RC_PATH"
        echo "  ✓ Added to $SHELL_RC"
    else
        echo "  ⚠ Skipped. Add manually:"
        echo "$LINES_TO_ADD" | while IFS= read -r line; do
            echo "    $line"
        done
    fi
fi
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
echo "  Restart your terminal (or run: source $SHELL_RC)"
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
