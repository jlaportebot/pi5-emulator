#!/usr/bin/env bash
# Mount the dmg and copy LobsterVM.app into ~/Applications; exports LOBSTERVM_CLI.
set -euo pipefail

installer_dir="$1"
dmg="$(find "$installer_dir" -name 'LobsterVM-*-arm64.dmg' -print -quit)"
test -n "$dmg"
mount_point="$(mktemp -d)"
hdiutil attach "$dmg" -nobrowse -readonly -mountpoint "$mount_point"
trap 'hdiutil detach "$mount_point" -force || true' EXIT
test -L "$mount_point/Applications"
mkdir -p "$HOME/Applications"
rm -rf "$HOME/Applications/LobsterVM.app"
ditto "$mount_point/LobsterVM.app" "$HOME/Applications/LobsterVM.app"
codesign --verify --deep --strict "$HOME/Applications/LobsterVM.app"
cli="$HOME/Applications/LobsterVM.app/Contents/MacOS/lobstervm-cli"
test -x "$cli"
echo "installed to $HOME/Applications/LobsterVM.app"
if [ -n "${GITHUB_ENV:-}" ]; then echo "LOBSTERVM_CLI=$cli" >>"$GITHUB_ENV"; fi
