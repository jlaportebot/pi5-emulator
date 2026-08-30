#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  LobsterOS Pi 5 Emulator — Headless Serial Console Launcher  (TERMINAL)
# ─────────────────────────────────────────────────────────────────────────────
#  Runs the bare-metal LobsterOS kernel on the Raspberry Pi 5 (BCM2712) machine
#  in QEMU with a pure serial console for interactive terminal use.
#
#  Pipeline supported from this shell:
#    wget / curl  - download files over HTTP       (host lobster-net proxy)
#    vi           - create/edit files in the VFS
#    cc <file.c>  - compile C source to a native AArch64 ELF (lobster-net)
#    run <elf>    - execute an ELF as a user process
#    run /bin/python - MicroPython interpreter
#    pkg          - .lobpkg package manager
# ─────────────────────────────────────────────────────────────────────────────

set -u

REPO=/home/john/pi5-emulator-repo
QEMU=/home/john/qemu-src/build/qemu-system-aarch64
KERNEL=/mnt/data/sd-overflow/LobsterOS/lobster-os/build/kernel8.img
DTB=$REPO/bcm2712-rpi-5-b.dtb

# ── precondition checks ──────────────────────────────────────────────────────
missing=0
for f in "$QEMU" "$KERNEL" "$DTB"; do
    if [ ! -f "$f" ]; then
        echo "ERROR: missing $f (run 'make FEATURES=qemu_raspi5b' in lobster-os, or rebuild emulator)."
        missing=1
    fi
done
[ "$missing" -ne 0 ] && { echo "Aborting."; exit 1; }

# ── environment (harmless for a headless console, kept for X/Wayland parity) ─
export DISPLAY="${DISPLAY:-:0}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"

cat <<'EOF'
╔══════════════════════════════════════════════════════════════════════╗
║  🦞 LobsterOS — Raspberry Pi 5 Emulator (Headless Shell)             ║
║  BCM2712 • 4× Cortex-A76 • 4 GB RAM • Custom QEMU                    ║
╚══════════════════════════════════════════════════════════════════════╝
Type 'help' for commands.  'cc file.c -run' to compile+run C,
'wget URL -O path' to download, 'run /bin/python' for MicroPython.
Ctrl+A X to quit QEMU.  Ctrl+C to interrupt a running program.
EOF
echo ""

# ── launch ──────────────────────────────────────────────────────────────────
# NOTE: raspi5b machine requires >= 4 vCPUs; omit -smp to use the default 4.
exec "$QEMU" \
  -M raspi5b \
  -m 4G \
  -cpu cortex-a76 \
  -kernel "$KERNEL" \
  -dtb "$DTB" \
  -serial stdio \
  -display none \
  -no-reboot \
  -no-shutdown