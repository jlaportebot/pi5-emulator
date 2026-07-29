#!/bin/bash
# LobsterOS Pi 5 Emulator — Desktop Launcher (Graphical QEMU with GTK display)
cd /home/john/pi5-emulator-repo

# Quick file checks
for f in \
    "/home/john/qemu-src/build/qemu-system-aarch64" \
    "/mnt/data/sd-overflow/LobsterOS/lobster-os/build/kernel8.img" \
    "/home/john/pi5-emulator-repo/bcm2712-rpi-5-b.dtb"; do
    if [ ! -f "$f" ]; then
        zenity --error --title="LobsterOS" --text="Missing:\n$f" 2>/dev/null || echo "ERROR: Missing $f"
        exit 1
    fi
done

export DISPLAY=:0
export WAYLAND_DISPLAY=wayland-0
export XDG_RUNTIME_DIR=/run/user/1000
exec python3 /home/john/pi5-emulator-repo/lobsteros_gui.py