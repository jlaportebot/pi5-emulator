#!/bin/bash
# Pi 5 Emulator Launch Script — Headless (no display, serial only)
# Usage: ./run_pi5.sh [kernel_image] [options]

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

echo "Starting Raspberry Pi 5 Emulator (headless, serial output)..."
echo "QEMU: $QEMU_BIN"
echo "Kernel: $KERNEL"

exec "$QEMU_BIN" \
    -M raspi5b \
    -m 4G \
    -cpu cortex-a72 \
    -kernel "$KERNEL" \
    -dtb "$DTB_FILE" \
    -serial stdio \
    -display none \
    -no-reboot \
    "$@"
