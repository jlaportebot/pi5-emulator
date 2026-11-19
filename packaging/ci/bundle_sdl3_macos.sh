#!/usr/bin/env bash
# The macOS QEMU archive links Homebrew's sdl2-compat, which dlopen()s the UNVERSIONED name
# "@loader_path/libSDL3.dylib" at load time (never libSDL3.0.dylib) and shows a modal
# "Failed loading SDL3 library" dialog when it is missing. Ship libSDL3 next to libSDL2 under that
# name until lobster-qemu bundles it, then prove the bundled copy is the one that gets loaded.
#   packaging/ci/bundle_sdl3_macos.sh <LobsterVM.app>
set -euo pipefail

app="$1"
qemu_dir="$(cd "$app/Contents/Resources/qemu" && pwd -P)"
qemu_bin="$qemu_dir/bin/qemu-system-aarch64"
brew install sdl3
real="$(brew --prefix sdl3)/lib/libSDL3.0.dylib"
test -f "$real"
for name in libSDL3.dylib libSDL3.0.dylib; do
  cp -L "$real" "$qemu_dir/lib/$name"
  chmod u+w "$qemu_dir/lib/$name"
  install_name_tool -id "@rpath/$name" "$qemu_dir/lib/$name"
  codesign --force --sign - --timestamp=none "$qemu_dir/lib/$name"
done
otool -L "$qemu_dir/lib/libSDL3.dylib"

# macOS has no GNU timeout; perl ships with the OS
libs="$(env -i PATH=/usr/bin:/bin DYLD_PRINT_LIBRARIES=1 \
  perl -e 'alarm shift @ARGV; exec @ARGV' 60 "$qemu_bin" --version 2>&1)"
echo "$libs" | grep -i "sdl3" || true
if ! echo "$libs" | grep -q "$qemu_dir/lib/libSDL3.dylib"; then
  echo "bundled libSDL3.dylib was not the copy loaded" >&2
  exit 1
fi
if echo "$libs" | grep -i "sdl3" | grep -q "/opt/homebrew"; then
  echo "a Homebrew libSDL3 was loaded instead" >&2
  exit 1
fi
