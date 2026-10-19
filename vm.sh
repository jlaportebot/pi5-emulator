#!/usr/bin/env bash
# vm.sh - LobsterOS VM manager (VirtualBox-style workflow)
#
#   vm.sh list                 - list snapshots on the working overlay
#   vm.sh snap <tag>            - save full machine state (RAM + disk) as <tag>
#   vm.sh boot [--resume tag]   - boot the VM; --resume restores <tag> first
#   vm.sh del <tag>             - delete a snapshot
#
# Snapshots live in lobsteros-overlay.qcow2 (qcow2 overlay on the pristine
# lobsteros.img). The base image stays untouched; disk writes and VM state
# land in the overlay.
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
BASE="$REPO/lobsteros.img"
OVERLAY="$REPO/lobsteros-overlay.qcow2"
QEMU=/mnt/data/sd-overflow/qemu-src/build-opt/qemu-system-aarch64
KERNEL="$REPO/kernel8.img"
DTB="$REPO/bcm2712-rpi-5-b.dtb"
MON=/tmp/lobster-vm.sock

ensure_overlay() {
    if [[ ! -f "$OVERLAY" ]]; then
        qemu-img create -f qcow2 -b "$BASE" -F raw "$OVERLAY"
        echo "[vm] created overlay: $OVERLAY"
    fi
}

case "${1:-list}" in
list)
    ensure_overlay
    qemu-img snapshot -l "$OVERLAY" || true
    ;;
snap)
    ensure_overlay
    TAG="${2:?usage: vm.sh snap <tag>}"
    rm -f "$MON"
    LBST_DISKIMG="$OVERLAY" "$QEMU" -M raspi5b,graphics=on -m 4G \
        -cpu cortex-a76 -kernel "$KERNEL" -dtb "$DTB" -display none \
        -no-reboot -no-shutdown -serial none \
        -monitor unix:"$MON",server,nowait &
    QPID=$!
    sleep 15
    printf 'savevm %s\nquit\n' "$TAG" | socat - UNIX-CONNECT:"$MON"
    wait $QPID 2>/dev/null || true
    echo "[vm] snapshot '$TAG' saved."
    ;;
boot)
    ensure_overlay
    ARGS=(-M raspi5b,graphics=on -m 4G -cpu cortex-a76 -kernel "$KERNEL" -dtb "$DTB"
          -display gtk -no-reboot -no-shutdown)
    if [[ "${2:-}" == "--resume" ]]; then
        TAG="${3:?usage: vm.sh boot --resume <tag>}"
        ARGS+=(-loadvm "$TAG")
        echo "[vm] booting overlay, resuming snapshot '$TAG'..."
    else
        echo "[vm] booting overlay..."
    fi
    exec env LBST_DISKIMG="$OVERLAY" "$QEMU" "${ARGS[@]}"
    ;;
del)
    ensure_overlay
    TAG="${2:?usage: vm.sh del <tag>}"
    qemu-img snapshot -d "$TAG" "$OVERLAY"
    echo "[vm] deleted '$TAG'."
    ;;
*)
    grep '^#' "$0" | head -12
    ;;
esac
