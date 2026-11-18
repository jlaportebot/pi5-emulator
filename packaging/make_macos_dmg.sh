#!/usr/bin/env bash
# Ad-hoc sign LobsterVM.app and wrap it into LobsterVM-<version>-arm64.dmg.
#   packaging/make_macos_dmg.sh <LobsterVM.app> <version> <out dir>
# QEMU runs with TCG only, so no hypervisor entitlement is needed.
set -euo pipefail

app="$(cd "$1" && pwd)"
version="$2"
mkdir -p "$3"
out="$(cd "$3" && pwd)"
dmg="$out/LobsterVM-$version-arm64.dmg"
plist="$app/Contents/Info.plist"

check_main_executable() {
  local name
  name="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$plist")"
  if [ "$name" != "LobsterVM" ] || [ ! -x "$app/Contents/MacOS/$name" ]; then
    echo "unexpected CFBundleExecutable '$name'" >&2
    exit 1
  fi
}

# Every Mach-O must carry a valid signature before it is loaded on Apple Silicon,
# including the QEMU binaries and dylibs copied in after PyInstaller signed its own.
sign_macho_files() {
  local count=0 file
  while IFS= read -r -d '' file; do
    if file -b "$file" | grep -q 'Mach-O'; then
      codesign --force --sign - --timestamp=none "$file"
      count=$((count + 1))
    fi
  done < <(find "$app" -type f -print0)
  echo "signed $count Mach-O files"
}

make_dmg() {
  local staging
  staging="$(mktemp -d)"
  cp -a "$app" "$staging/"
  ln -s /Applications "$staging/Applications"
  rm -f "$dmg"
  hdiutil create -volname "LobsterVM" -srcfolder "$staging" -ov -format UDZO "$dmg"
  rm -rf "$staging"
}

check_main_executable
sign_macho_files
codesign --force --sign - --timestamp=none "$app"
codesign --verify --deep --strict --verbose=2 "$app"
make_dmg
echo "dmg: $dmg"
