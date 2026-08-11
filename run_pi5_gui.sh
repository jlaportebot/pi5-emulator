#!/bin/bash
# Pi 5 Emulator Launch Script — Direct QEMU (no Tk panel)
# Usage: ./run_pi5_gui.sh [kernel_image] [options]
# Opens a GTK graphical display showing the emulated framebuffer.

set -e

QEMU_BIN="/home/john/qemu-src/build/qemu-system-aarch64"
DEFAULT_KERNEL="/mnt/data/sd-overflow/LobsterOS/lobster-os/build/kernel8.img"
DTB_FILE="/home/john/pi5-emulator-repo/bcm2712-rpi-5-b.dtb"

KERNEL="${1:-$DEFAULT_KERNEL}"
shift || true

if [ ! -f "$QEMU_BIN" ]; then
    echo "Error: QEMU binary not found at $QEMU_BIN"
    exit 1
fi
if [ ! -f "$KERNEL" ]; then
    echo "Error: Kernel image not found at $KERNEL"
    exit 1
fi
if [ ! -f "$DTB_FILE" ]; then
    echo "Error: DTB file not found at $DTB_FILE"
    exit 1
fi

export DISPLAY=:0
export WAYLAND_DISPLAY=wayland-0
export XDG_RUNTIME_DIR=/run/user/1000

echo "Starting Raspberry Pi 5 Emulator (graphical)..."
echo "QEMU: $QEMU_BIN"
echo "Kernel: $KERNEL"
echo "DTB: $DTB_FILE"

exec "$QEMU_BIN" \
    -M raspi5b,graphics=on \
    -m 4G \
    -cpu cortex-a76 \
    -kernel "$KERNEL" \
    -dtb "$DTB_FILE" \
    -serial stdio \
    -display gtk,show-tabs=off,show-menubar=off,show-cursor=on,grab-on-hover=on \
    -device usb-mouse \
    -device usb-kbd \
    -no-reboot \
    -no-shutdown \
    "$@"
