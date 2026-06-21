# Raspberry Pi 5 (BCM2712) QEMU Emulator

A QEMU-based emulator for the Raspberry Pi 5 (BCM2712 SoC).

## Overview

This repository contains the QEMU source files necessary for Raspberry Pi 5 (BCM2712) emulation, along with device trees, kernel images, and boot scripts.

## Status

**Device Realization**: ✅ Complete - All devices realize successfully
**Kernel Boot**: ⚠️ In Progress - Device realization works, kernel boot needs debugging

See [STATUS.md](STATUS.md) for detailed status.

## Quick Start

### Prerequisites
```bash
# Ubuntu/Debian
sudo apt-get install git ninja-build pkg-config libglib2.0-dev libpixman-1-dev \
    libfdt-dev libslirp-dev libcapstone-dev libseccomp-dev \
    python3 python3-venv python3-pip

# For GTK display support
sudo apt-get install libgtk-3-dev
```

### Build
```bash
./configure --target-list=aarch64-softmmu --enable-debug --enable-gtk
ninja -C build
```

### Run with LobsterOS (bare-metal)
```bash
./build/qemu-system-aarch64 -M raspi5b -m 4G -cpu cortex-a76 -smp 4 \
    -kernel kernel8.img -serial stdio -display none
```

### Run with Raspberry Pi OS
```bash
./build/qemu-system-aarch64 -M raspi5b -m 4G -cpu cortex-a76 -smp 4 \
    -kernel Image-pi5 -initrd initramfs8 -dtb bcm2712-rpi-5-b.dtb \
    -serial stdio -display none \
    -append "console=ttyAMA0,115200 root=/dev/ram0 rw"
```

## Repository Structure

```
pi5-emulator/
├── hw/
│   ├── arm/
│   │   ├── bcm2712.c              # BCM2712 SoC
│   │   ├── bcm2712_peripherals.c  # Peripherals container
│   │   ├── bcm2712_rp1.c          # RP1 I/O controller
│   │   ├── bcm2712_v3d.c          # V3D GPU placeholder
│   │   ├── bcm2712_v3d.c          # Pi 5 machine
│   │   ├── bcm2835_peripherals.c  # Base peripherals (modified)
│   │   ├── bcm2836.c              # Pi 3/4 support
│   │   ├── raspi.c                # Base machine (modified)
│   │   └── raspi5b.c              # Pi 5 machine
│   ├── display/
│   │   └── bcm2835_fb.c           # Framebuffer (modified)
│   ├── misc/
│   │   └── bcm2835_cprman.c       # CPRMAN clock manager
│   └── pci-host/
│       └── bcm_pcie.c             # PCIe controller
├── include/
│   ├── hw/arm/
│   │   ├── bcm2712.h
│   │   ├── bcm2712_peripherals.h
│   │   ├── bcm2712_rp1.h
│   │   ├── bcm2712_v3d.h
│   │   └── bcm2712_peripherals.h
│   └── hw/display/
│       └── bcm2835_fb.h
├── bcm2712-rpi-5-b.dtb            # Pi 5 device tree
├── Image-pi5                       # Raspberry Pi OS kernel
├── initramfs8                      # Raspberry Pi OS initramfs
├── kernel8.img                     # LobsterOS kernel
├── run_pi5.sh                      # LobsterOS launch script
├── run_pi5_gui.sh                  # GUI launch script
└── STATUS.md                       # Detailed status
```

## Status

**Device Realization**: ✅ Complete - All devices realize successfully
**Kernel Boot**: ⚠️ In Progress - Device realization works, kernel boot needs debugging

See [STATUS.md](STATUS.md) for detailed status.