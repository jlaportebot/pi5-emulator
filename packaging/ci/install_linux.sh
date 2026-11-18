#!/usr/bin/env bash
# "Install" the AppImage as ~/Applications/LobsterVM.AppImage; exports LOBSTERVM_CLI.
# Needs FUSE 2 to mount itself; Ubuntu 24.04 does not ship it by default.
set -euo pipefail

installer_dir="$1"
appimage="$(find "$installer_dir" -name 'LobsterVM-*.AppImage' -print -quit)"
test -n "$appimage"
sudo apt-get update
sudo apt-get install -y libfuse2t64
mkdir -p "$HOME/Applications"
install -m 755 "$appimage" "$HOME/Applications/LobsterVM.AppImage"
echo "installed to $HOME/Applications/LobsterVM.AppImage"
if [ -n "${GITHUB_ENV:-}" ]; then
  echo "LOBSTERVM_CLI=$HOME/Applications/LobsterVM.AppImage" >>"$GITHUB_ENV"
fi
