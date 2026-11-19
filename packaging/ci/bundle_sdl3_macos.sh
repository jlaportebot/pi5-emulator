#!/usr/bin/env bash
# The macOS QEMU archive links Homebrew's sdl2-compat, which dlopen()s libSDL3 at load time
# without the archive shipping it ("Failed loading SDL3 library" modal dialog, QEMU hangs).
# Copy libSDL3 next to libSDL2 until lobster-qemu bundles it, then prove QEMU starts.
#   packaging/ci/bundle_sdl3_macos.sh <LobsterVM.app>
set -euo pipefail

app="$1"
qemu_dir="$app/Contents/Resources/qemu"
brew install sdl3
sdl3="$(brew --prefix sdl3)/lib/libSDL3.0.dylib"
test -f "$sdl3"
cp -L "$sdl3" "$qemu_dir/lib/libSDL3.0.dylib"
chmod u+w "$qemu_dir/lib/libSDL3.0.dylib"
otool -L "$qemu_dir/lib/libSDL3.0.dylib"
codesign --force --sign - --timestamp=none "$qemu_dir/lib/libSDL3.0.dylib"
# macOS has no GNU timeout; perl ships with the OS
env -i PATH=/usr/bin:/bin perl -e 'alarm shift @ARGV; exec @ARGV' 60 "$qemu_dir/bin/qemu-system-aarch64" --version
