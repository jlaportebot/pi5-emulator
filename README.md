# Raspberry Pi 5 (BCM2712) QEMU Emulator

A QEMU-based emulator for the Raspberry Pi 5 (BCM2712 SoC).

## Overview

This repository contains the QEMU source files necessary for Raspberry Pi 5 (BCM2712) emulation, along with device trees, kernel images, and boot scripts.

## Status

**Device Realization**: ✅ Complete - All devices realize successfully without crashes
**Kernel Boot**: ⚠️ In Progress - Device realization works, kernel boot needs debugging (GIC duplicate property warnings, no console output yet)

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
│   │   ├── bcm2712.c              # BCM2712 SoC initialization
│   │   ├── bcm2712_peripherals.c  # BCM2712 peripherals container
│   │   ├── bcm2712_rp1.c          # RP1 I/O controller
│   │   ├── bcm2712_v3d.c          # V3D GPU placeholder
│   │   ├── bcm2835_cprman.c       # CPRMAN clock manager
│   │   ├── bcm2835_fb.c           # Framebuffer (modified)
│   │   ├── bcm2835_peripherals.c  # Base peripherals (modified)
│   │   ├── bcm2836.c              # Pi 3/4 support
│   │   ├── raspi.c                # Base machine (modified)
│   │   ├── raspi5b.c              # Pi 5 machine
│   │   └── bcm_pcie.c             # PCIe controller
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
│   │   └── bcm2835_fb.h
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

## Implemented Devices ✅

- **BCM2712 SoC**: 4× Cortex-A76 cores, GIC-400 interrupt controller
- **Memory System**: 4GB RAM (configurable), proper memory mapping at 0x7c000000 and 0x107c000000
- **Framebuffer**: V3D GPU framebuffer with mailbox channel alias technique
- **Peripherals**:
  - OTP (One-Time Programmable memory)
  - Property channel (mailbox)
  - SDHCI (EMMC1) - SD card controller
  - SDHOST - legacy SD controller
  - DMA channels (0 and 15)
  - DWC2 USB OTG
  - Power Management
  - SPI (SPI0)
  - I2C (3 controllers: BSC0, BSC1, BSC2)
  - UART0 (PL011) with Pi 5 address relocation (0x7d001000) and legacy alias (0xFE201000)
  - EMMC2 - Pi 5 microSD controller
  - RP1 I/O controller (GPIO, UART, I2C, SPI, SD)
  - V3D GPU (unimplemented placeholder)
- **Interrupt Controller**:
  - GIC-400: 4 CPU cores, 512 SPI interrupts, virtualization extensions
  - BCM2835 IC: Legacy interrupt controller for VideoCore peripherals
- **Clock Management**: CPRMAN with UART clock (3MHz from XOSC)
- **DMA Remap**: 0xc0000000 → 0x00000000 (30-bit DMA)

## Disabled/Unimplemented Devices ⚠️

- **GENET Ethernet**: Disabled (qdev_realize parent_bus conflict)
- **XHCI USB 3.0**: Disabled
- **PCIe Controllers**: Disabled (BCM PCIe driver has compilation issues)
- **HDMI Controllers**: Disabled
- **Audio Controller**: Disabled
- **V3D GPU**: Placeholder only (no 3D acceleration)
- **L2 INTCs**: Unimplemented placeholders

## Known Issues

1. **GIC Duplicate Property Warnings**: Non-fatal ERROR logs during GIC realize (`gic_cpu[0]`, `gic_viface[0]` duplicates)
2. **Kernel Boot**: No console output from kernel - likely GIC/interrupt delivery or device tree issues
3. **Missing Device Tree Bindings**: Many Pi 5 specific DT bindings not yet implemented

## Next Steps

1. **Fix GIC Duplicate Properties**: Investigate and resolve CPU interface property duplication
2. **Enable Kernel Boot**: Debug UART output, device tree, bootloader setup
3. **Enable GENET Ethernet**: Fix qdev_realize parent_bus conflict
4. **Enable XHCI/PCIe**: Fix BCM PCIe driver compilation
5. **Implement V3D GPU**: Basic framebuffer acceleration
6. **Test Raspberry Pi OS**: Full boot with graphical output

## Repository

Private repository with only Pi 5 emulator files (not full QEMU tree) to be created.