# Raspberry Pi 5 (BCM2712) QEMU Emulator - Status Report

## Summary
Successfully implemented device realization for Raspberry Pi 5 (BCM2712) in QEMU. The emulator now successfully realizes all devices without errors.

## What Works ✅

### Device Realization (All Working)
- **BCM2712 SoC**: 4× Cortex-A76 cores, GIC-400 interrupt controller
- **Memory System**: 4GB RAM (configurable), proper memory mapping
- **Framebuffer**: V3D GPU framebuffer with alias technique for mailbox channel
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
  - UART0 (PL011) with Pi 5 address relocation
  - EMMC2 - Pi 5 microSD controller
  - RP1 I/O controller (GPIO, UART, I2C, SPI, SD)
  - V3D GPU (unimplemented placeholder)
- **Unimplemented Device Placeholders**: txp, sp804, i2s, smi, spis, dbus, ave0, v3d, sdramc

### Interrupt Controller
- **GIC-400**: 4 CPU cores, 512 SPI interrupts, virtualization extensions
- **BCM2835 IC**: Legacy interrupt controller for VideoCore peripherals (64 GPU IRQs, 8 ARM IRQs)
- **IRQ Routing**: Properly connected for all devices

### Memory Management
- **Peripheral Memory Map**: Properly mapped at 0x7c000000 (32-bit) and 0x107c000000 (SCB bus)
- **GPU Bus**: Separate address space for VideoCore peripherals
- **DMA Remap**: 0xc0000000 → 0x00000000 (30-bit DMA)
- **Mailbox**: Framebuffer and property channels

### Clock Management
- **CPRMAN**: UART clock (3MHz from XOSC) properly connected

## Known Issues ⚠️

### Kernel Boot
- **Status**: Device realization completes successfully, but kernel doesn't produce output
- **Likely Causes**:
  - Device tree configuration (UART address, interrupts)
  - UART driver initialization in kernel
  - Kernel command line / bootloader setup
  - Device tree modifications needed for Pi 5 specific addresses

### Disabled Devices
- **GENET Ethernet**: qdev_realize parent_bus conflict (disabled with #if 0)
- **XHCI USB 3.0**: Disabled
- **PCIe Controllers**: Disabled (BCM PCIe driver has compilation issues)
- **HDMI Controllers**: Disabled
- **Audio Controller**: Disabled

## Files Modified/Added (15 files)

### Core Pi 5 Files (New)
- `hw/arm/bcm2712.c` - BCM2712 SoC initialization
- `hw/arm/bcm2712_peripherals.c` - BCM2712 peripherals container
- `hw/arm/bcm2712_rp1.c` - RP1 I/O controller
- `hw/arm/bcm2712_v3d.c` - V3D GPU placeholder
- `hw/arm/raspi5b.c` - Pi 5 machine definition
- `include/hw/arm/bcm2712.h` - BCM2712 definitions
- `include/hw/arm/bcm2712_peripherals.h` - Peripherals header
- `include/hw/arm/bcm2712_rp1.h` - RP1 header
- `include/hw/arm/bcm2712_v3d.h` - V3D header

### Modified Existing Files
- `hw/arm/bcm2835_peripherals.c` - Fixed framebuffer, added container management for all devices
- `hw/arm/bcm2835_fb.c` - Framebuffer init without sysbus_init_mmio
- `hw/arm/bcm2835_cprman.c` - CPRMAN clock fixes
- `hw/arm/raspi5b.c` - Pi 5 machine, DTB modification
- `hw/arm/bcm2712.c` - GIC-400, CPU, EMMC2, RP1, UART0
- `hw/arm/bcm2712_peripherals.c` - EMMC2, RP1, GENET (disabled), IRQ routing
- `hw/arm/bcm2835_peripherals.c` - Framebuffer alias, device container management
- `hw/arm/bcm2836.c` - Pi 5 compatibility
- `hw/arm/raspi.c` - Base machine fixes
- `include/hw/arm/bcm2712_peripherals.h` - Added fb_iomem_alias
- `include/hw/display/bcm2835_fb.h` - Added fb_iomem_alias field
- `hw/misc/bcm2835_cprman.c` - Clock fixes
- `hw/pci-host/bcm_pcie.c` - PCIe compilation fixes
- `qom/object.c` - Minor fix

## Build & Test
```bash
cd /home/john/pi5-emulator/qemu
./configure --target-list=aarch64-softmmu --enable-debug --enable-gtk
ninja -C build

# Run with LobsterOS (bare-metal)
./build/qemu-system-aarch64 -M raspi5b -m 4G -cpu cortex-a76 -smp 4 \
    -kernel /home/john/lobster-os/build/kernel8.img \
    -serial stdio -display none

# Run with Raspberry Pi OS
./build/qemu-system-aarch64 -M raspi5b -m 4G -cpu cortex-a76 -smp 4 \
    -kernel Image-pi5 -initrd initramfs8 -dtb bcm2712-rpi-5-b.dtb \
    -serial stdio -display none \
    -append "console=ttyAMA0,115200 root=/dev/ram0 rw"
```

## Next Steps
1. **Fix Kernel Boot**: Debug UART output, device tree, bootloader
2. **Enable GENET**: Fix qdev_realize parent_bus issue
3. **Enable XHCI/PCIe**: Fix BCM PCIe driver compilation
4. **Enable HDMI/Audio**: Implement display/audio controllers
5. **Clean Debug Prints**: Remove all fprintf(stderr, "DEBUG:") statements
6. **Test Raspberry Pi OS**: Full boot with graphical output

## Repository
Private repository with only Pi 5 emulator files (not full QEMU tree) to be created.