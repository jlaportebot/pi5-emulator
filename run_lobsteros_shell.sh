#!/bin/bash
# LobsterOS Pi 5 Emulator — Headless Serial Console Launcher
# Runs QEMU with -display none and serial stdio for pure shell interaction
cd /home/john/pi5-emulator-repo

# Quick file checks
for f in \
    "/home/john/qemu-src/build/qemu-system-aarch64" \
    "/mnt/data/sd-overflow/LobsterOS/lobster-os/build/kernel8.img" \
    "/home/john/pi5-emulator-repo/bcm2712-rpi-5-b.dtb"; do
    if [ ! -f "$f" ]; then
        echo "ERROR: Missing $f"
        exit 1
    fi
done

export DISPLAY=:0
export WAYLAND_DISPLAY=wayland-0
export XDG_RUNTIME_DIR=/run/user/1000

echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║  🦞 LobsterOS — Raspberry Pi 5 Emulator (Headless Shell)      ║"
echo "║  BCM2712 • 4× Cortex-A76 • 4 GB RAM • Custom QEMU             ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""
echo "Booting LobsterOS serial console... (no graphical window)"
echo "Type 'help' for commands. Ctrl+C to exit."
echo ""

exec /home/john/qemu-src/build/qemu-system-aarch64 \
  -M raspi5b \
  -m 4G \
  -cpu cortex-a72 \
  -kernel /mnt/data/sd-overflow/LobsterOS/lobster-os/build/kernel8.img \
  -dtb /home/john/pi5-emulator-repo/bcm2712-rpi-5-b.dtb \
  -serial stdio \
  -display none \
  -no-reboot \
  -no-shutdown