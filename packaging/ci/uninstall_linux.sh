#!/usr/bin/env bash
# Remove the AppImage and the copy it unpacked into the user's data dir.
set -euo pipefail

rm -f "$HOME/Applications/LobsterVM.AppImage"
rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/lobstervm/app"
if [ -e "$HOME/Applications/LobsterVM.AppImage" ]; then
  echo "AppImage still exists" >&2
  exit 1
fi
echo "uninstalled: AppImage and unpacked copy are gone"
