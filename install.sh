#!/usr/bin/env bash
# install.sh — Install LobsterVM into ~/.local for desktop integration
# Run from the repository root: ./install.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"

echo "Installing LobsterVM from $REPO_ROOT"

# Create directories
mkdir -p "$BIN_DIR" "$APP_DIR" "$ICON_DIR"

# Symlink lobstervm.sh onto PATH
ln -sf "$REPO_ROOT/lobstervm.sh" "$BIN_DIR/lobstervm"
echo "Linked lobstervm -> $BIN_DIR/lobstervm"

# Copy .desktop launcher
cp "$REPO_ROOT/lobstervm-gui.desktop" "$APP_DIR/lobstervm-gui.desktop"
echo "Installed desktop entry -> $APP_DIR/lobstervm-gui.desktop"

# Copy icon (use the existing shell icon if no dedicated icon exists)
if [[ -f "$REPO_ROOT/assets/lobsteros-shell-icon.png" ]]; then
    cp "$REPO_ROOT/assets/lobsteros-shell-icon.png" "$ICON_DIR/lobstervm.png"
    echo "Installed icon -> $ICON_DIR/lobstervm.png"
fi

# Update desktop database
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$APP_DIR" 2>/dev/null || true
fi

# Update icon cache
if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi

echo ""
echo "Installation complete!"
echo "  - Run 'lobstervm' from any terminal (ensure ~/.local/bin is in your PATH)"
echo "  - Find 'LobsterVM Manager' in your application menu"
echo ""
echo "To uninstall, run: $REPO_ROOT/uninstall.sh"