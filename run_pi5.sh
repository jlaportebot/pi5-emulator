#!/bin/bash
# Pi 5 Emulator Launch Script
# Usage: ./run_pi5.sh [kernel_image] [disk_image] [options]

set -e

QEMU_BIN="/home/john/pi5-emulator/qemu/build/qemu-system-aarch64"
DEFAULT_KERNEL="/home/john/lobster-os/build/kernel8.img"
DEFAULT_DISK="/home/john/pi5-emulator/2024-11-19-raspios-bookworm-arm64-lite.img"

KERNEL="${1:-$DEFAULT_KERNEL}"
DISK="${2:-$DEFAULT_DISK}"
shift 2 || true

if [ ! -f "$QEMU_BIN" ]; then
    echo "Error: QEMU binary not found at $QEMU_BIN"
    echo "Please build the Pi 5 emulator first."
    exit 1
fi

if [ ! -f "$KERNEL" ]; then
    echo "Error: Kernel image not found at $KERNEL"
    exit 1
fi

if [ ! -f "$DISK" ]; then
    echo "Error: Disk image not found at $DISK"
    exit 1
fi

echo "Starting Raspberry Pi 5 Emulator..."
echo "QEMU: $QEMU_BIN"
echo "Kernel: $KERNEL"
echo "Disk: $DISK"
echo "Machine: raspi5b"
echo ""

exec "$QEMU_BIN" \
    -M raspi5b \
    -m 4G \
    -cpu cortex-a76 \
    -smp 4 \
    -kernel "$KERNEL" \
    -drive file="$DISK",format=raw,if=sd \
    -display none \
    "$@"