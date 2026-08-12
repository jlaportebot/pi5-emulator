#!/bin/bash
# LobsterOS Pi 5 Emulator — Graphical Desktop Launcher
# Directly launches QEMU with GTK display (no Tkinter wrapper needed)
cd /home/john/pi5-emulator-repo

QEMU="/home/john/qemu-src/build/qemu-system-aarch64"
KERNEL="/mnt/data/sd-overflow/LobsterOS/lobster-os/build/kernel8.img"
DTB="/home/john/pi5-emulator-repo/bcm2712-rpi-5-b.dtb"

# Quick file checks
for f in "$QEMU" "$KERNEL" "$DTB"; do
    if [ ! -f "$f" ]; then
        echo "ERROR: Missing $f"
        if command -v zenity &>/dev/null; then
            zenity --error --title="LobsterOS" --text="Missing:\n$f"
        fi
        exit 1
    fi
done

export DISPLAY=:0
export WAYLAND_DISPLAY=wayland-0
export XDG_RUNTIME_DIR=/run/user/1000

echo "Starting LobsterOS GUI..."
echo "  Kernel: $KERNEL"
echo "  DTB:    $DTB"
echo ""

exec "$QEMU" \
    -M raspi5b \
    -m 4G \
    -cpu cortex-a72 \
    -kernel "$KERNEL" \
    -dtb "$DTB" \
    -display gtk,show-cursor=on \
    -serial null \
    -no-reboot \
    -no-shutdown
