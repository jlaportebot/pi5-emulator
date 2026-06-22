# Raspberry Pi 5 (BCM2712) QEMU Emulator - Status Report

## Summary
Successfully implemented device realization for Raspberry Pi 5 (BCM2712) in QEMU. The emulator now successfully realizes all devices without crashes or assertion failures.

## What Works ✅

### Device Realization (All Working)
- **BCM2712 SoC**: 4× Cortex-A76 cores, GIC-400 interrupt controller
- **Memory System**: 4GB RAM (configurable), proper memory mapping at 0x7c000000 (32-bit) and 0x107c000000 (SCB bus)
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
  - UART0 (PL011) with Pi 5 address relocation (0x7d001000) and legacy alias (0xFE201000)
  - EMMC2 - Pi 5 microSD controller
  - RP1 I/O controller (GPIO, UART, I2C, SPI, SD)
  - V3D GPU (unimplemented placeholder)
- **Interrupt Controller**:
  - GIC-400: 4 CPU cores, 512 SPI interrupts, virtualization extensions
  - BCM2835 IC: Legacy interrupt controller for VideoCore peripherals (64 GPU IRQs, 8 ARM IRQs)
- **Clock Management**: CPRMAN with UART clock (3MHz from XOSC) properly connected
- **DMA Remap**: 0xc0000000 → 0x00000000 (30-bit DMA)

### Memory Mapping
- Peripheral region at 0x7c000000 (32-bit alias) with overlap priority 1
- SCB bus at 0x107c000000+
- GPU peripheral alias at 0x7e000000
- Legacy UART0 at 0xFE201000 (via alias)

## Known Issues ⚠️

### GIC Duplicate Property Warnings
- **Status**: Non-fatal ERROR logs during GIC realize
- **Details**: `duplicate property 'gic_cpu[0]'`, `gic_viface[0]` etc. on object type 'arm_gic'
- **Impact**: GIC may not function correctly for interrupt delivery
- **Likely Cause**: CPU interface MMIO regions created with duplicate names "gic_cpu"/"gic_viface"

### Kernel Boot - No Console Output
- **Status**: Device realization completes, but kernel produces no serial output
- **Tested With**: LobsterOS (kernel8.img) and Raspberry Pi OS (Image-pi5 + initramfs8 + DTB)
- **Likely Causes**:
  - GIC interrupt delivery not working due to duplicate property issue
  - Device tree missing/mismatched bindings for emulated hardware
  - Bootloader/setup code issues for bare-metal kernels
  - Timer interrupts not delivered

### Disabled Devices
- **GENET Ethernet**: qdev_realize parent_bus conflict (disabled with #if 0)
- **XHCI USB 3.0**: Disabled
- **PCIe Controllers**: 3 lanes, BCM PCIe driver has compilation issues (disabled)
- **HDMI Controllers**: 2× HDMI, disabled
- **Audio Controller**: Disabled
- **V3D GPU**: Placeholder only, no 3D acceleration
- **L2 INTCs**: Unimplemented placeholders (disabled IRQ connections)

## Files Modified/Fixed (Latest Commit)

### Core Fixes
1. **bcm2712.c**: Fixed memory region container handling, use standard sysbus_mmio_map_overlap, UART0 alias for legacy address
2. **bcm2712_peripherals.c**: Fixed peri_alias_mr name conflict, removed unused child peri_mr init
3. **IRQ Connections**: Wrapped all unimplemented device IRQ connections in #if 0

## Build & Test
```bash
cd /home/john/pi5-emulator/qemu
./configure --target-list=aarch64-softmmu --enable-debug --enable-gtk
ninja -C build

# Test with LobsterOS
./build/qemu-system-aarch64 -M raspi5b -m 4G -cpu cortex-a76 -smp 4 \
    -kernel /home/john/pi5-emulator/kernel8.img -serial stdio -display none

# Test with Raspberry Pi OS
./build/qemu-system-aarch64 -M raspi5b -m 4G -cpu cortex-a76 -smp 4 \
    -kernel Image-pi5 -initrd initramfs8 -dtb bcm2712-rpi-5-b.dtb \
    -serial stdio -display none \
    -append "console=ttyAMA0,115200 root=/dev/ram0 rw"
```

## Next Steps (Priority Order)

1. **Fix GIC Duplicate Properties** - Investigate arm_gic CPU interface creation
2. **Debug Kernel Boot** - Add GDB stub, trace CPU execution, verify interrupt delivery
3. **Enable GENET Ethernet** - Fix qdev_realize parent_bus conflict
4. **Enable XHCI/PCIe** - Fix BCM PCIe driver compilation
5. **Implement Device Tree Bindings** - Match Linux DT expectations
6. **Test Raspberry Pi OS Full Boot** - With graphical output via V3D/framebuffer

## Repository
- GitHub: https://github.com/jlaportebot/pi5-emulator
- Main QEMU tree: /home/john/pi5-emulator/qemu/