#!/bin/bash
# Install the family-manager worker as a macOS LaunchAgent so it auto-starts
# at login, stays running in the background, and restarts if it crashes.
#
# Usage:
#   FAMILY_WORKER_TOKEN=<fly worker secret> \
#   LLM_API_KEY=<sk-... Anthropic-compatible key> \
#   LLM_BASE_URL=https://tdyun.ai  # optional, defaults to api.anthropic.com
#   LLM_MODEL=claude-sonnet-4-6    # optional
#   ./install.sh
#
# Reinstall: re-run with the same env. Uninstall: launchctl bootout + delete plist.

set -e

if [ -z "$FAMILY_WORKER_TOKEN" ]; then
  echo "error: FAMILY_WORKER_TOKEN is required" >&2
  exit 1
fi

if [ -z "$LLM_API_KEY" ]; then
  echo "error: LLM_API_KEY is required (Anthropic-compatible sk-... key)" >&2
  exit 1
fi

LLM_BASE_URL="${LLM_BASE_URL:-https://api.anthropic.com}"
LLM_MODEL="${LLM_MODEL:-claude-sonnet-4-6}"

LABEL="com.gw.family-manager.worker"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKER_SCRIPT="$SCRIPT_DIR/worker.py"
LOG_DIR="$HOME/Library/Logs/family-manager"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$LOG_DIR"

# Find a usable python3 (system, brew, pyenv — whatever we can see)
PYTHON_BIN="$(command -v python3)"
if [ -z "$PYTHON_BIN" ]; then
  echo "error: python3 not found in PATH" >&2
  exit 1
fi

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON_BIN</string>
    <string>$WORKER_SCRIPT</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>FAMILY_WORKER_TOKEN</key><string>$FAMILY_WORKER_TOKEN</string>
    <key>FAMILY_API</key><string>${FAMILY_API:-https://gw-family-manager.fly.dev}</string>
    <key>LLM_API_KEY</key><string>$LLM_API_KEY</string>
    <key>LLM_BASE_URL</key><string>$LLM_BASE_URL</string>
    <key>LLM_MODEL</key><string>$LLM_MODEL</string>
    <key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/worker.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/worker.err</string>
</dict>
</plist>
EOF

# Reload if already loaded (ignore error if not loaded yet)
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo "installed: $PLIST_PATH"
echo "logs:      $LOG_DIR/worker.log"
echo ""
echo "verify:    launchctl print gui/$(id -u)/$LABEL | head -20"
echo "stop:      launchctl bootout gui/$(id -u)/$LABEL"
echo "tail log:  tail -f $LOG_DIR/worker.log"
