#!/usr/bin/env bash
# uninstall.sh — Remove LobsterVM desktop integration
# Run from the repository root: ./uninstall.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"

echo "Uninstalling LobsterVM desktop integration"

# Remove symlink
rm -f "$BIN_DIR/lobstervm"
echo "Removed $BIN_DIR/lobstervm"

# Remove desktop entry
rm -f "$APP_DIR/lobstervm-gui.desktop"
echo "Removed $APP_DIR/lobstervm-gui.desktop"

# Remove icon
rm -f "$ICON_DIR/lobstervm.png"
echo "Removed $ICON_DIR/lobstervm.png"

# Update desktop database
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$APP_DIR" 2>/dev/null || true
fi

# Update icon cache
if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi

echo "Uninstall complete."