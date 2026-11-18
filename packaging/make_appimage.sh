#!/usr/bin/env bash
# Wrap a built Linux bundle into LobsterVM-<version>-<arch>.AppImage.
#   packaging/make_appimage.sh <bundle dir> <version> <out dir>
# The AppImage starts the GUI; `<AppImage> cli <args>` runs the CLI instead.
set -euo pipefail

APPIMAGETOOL_VERSION=1.9.0
APPIMAGETOOL_URL=https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}
declare -A APPIMAGETOOL_SHA256=(
  [x86_64]=46fdd785094c7f6e545b61afcfb0f3d98d8eab243f644b4b17698c01d06083d1
  [aarch64]=04f45ea45b5aa07bb2b071aed9dbf7a5185d3953b11b47358c1311f11ea94a96
)

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bundle="$(cd "$1" && pwd)"
version="$2"
mkdir -p "$3"
out="$(cd "$3" && pwd)"
arch="$(uname -m)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fetch_appimagetool() {
  local tool="$work/appimagetool"
  curl -fsSL -o "$tool" "$APPIMAGETOOL_URL/appimagetool-$arch.AppImage"
  echo "${APPIMAGETOOL_SHA256[$arch]}  $tool" | sha256sum -c - >&2
  chmod +x "$tool"
  echo "$tool"
}

# QEMU outlives the launcher, but an AppImage's FUSE mount disappears when the
# launcher exits (a running QEMU then dies with SIGBUS on its next unmapped page).
# So AppRun unpacks the payload once into the user's data dir and runs from there.
write_apprun() {
  cat >"$1/AppRun" <<'APPRUN'
#!/bin/sh
set -eu
here="$(dirname "$(readlink -f "$0")")"
payload="$here/usr/lib/LobsterVM"
build_id="$(sha256sum "$payload/share/lobsteros/VERSIONS.txt" | cut -c1-16)"
cache="${XDG_DATA_HOME:-$HOME/.local/share}/lobstervm/app"
app="$cache/$build_id"
if [ ! -x "$app/LobsterVM" ]; then
  mkdir -p "$cache"
  staging="$(mktemp -d "$cache/.unpack.XXXXXX")"
  cp -a "$payload/." "$staging/"
  if ! mv "$staging" "$app" 2>/dev/null; then
    rm -rf "$staging"
  fi
  find "$cache" -mindepth 1 -maxdepth 1 -regex '.*/[0-9a-f]\{16\}' ! -name "$build_id" \
    -exec rm -rf {} +
fi
if [ "${1:-}" = "cli" ]; then
  shift
  exec "$app/lobstervm-cli" "$@"
fi
exec "$app/LobsterVM" "$@"
APPRUN
  chmod +x "$1/AppRun"
}

write_desktop_entry() {
  cat >"$1/lobstervm.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=LobsterVM
Comment=Run LobsterOS in a virtual machine
Exec=AppRun
Icon=lobstervm
Categories=System;Emulator;
Terminal=false
DESKTOP
}

appdir="$work/LobsterVM.AppDir"
mkdir -p "$appdir/usr/lib"
cp -a "$bundle" "$appdir/usr/lib/LobsterVM"
python3 "$here/make_icons.py" --out "$work/icons" >/dev/null
cp "$work/icons/lobstervm.png" "$appdir/lobstervm.png"
cp "$work/icons/lobstervm.png" "$appdir/.DirIcon"
write_apprun "$appdir"
write_desktop_entry "$appdir"
tool="$(fetch_appimagetool)"
target="$out/LobsterVM-$version-$arch.AppImage"
ARCH="$arch" APPIMAGE_EXTRACT_AND_RUN=1 "$tool" --no-appstream "$appdir" "$target"
chmod +x "$target"
echo "appimage: $target"
