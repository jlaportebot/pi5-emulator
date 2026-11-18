#!/usr/bin/env bash
# Uninstalling on macOS is dragging the app to the Trash; do the same and assert it is gone.
set -euo pipefail

rm -rf "$HOME/Applications/LobsterVM.app"
if [ -e "$HOME/Applications/LobsterVM.app" ]; then
  echo "LobsterVM.app still exists" >&2
  exit 1
fi
echo "uninstalled: LobsterVM.app is gone"
