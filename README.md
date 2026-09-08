# Raspberry Pi 5 (BCM2712) QEMU Emulator

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![QEMU](https://img.shields.io/badge/QEMU-9.0+-orange.svg)](https://www.qemu.org/)
[![Target](https://img.shields.io/badge/target-raspi5b-blue.svg)](https://github.com/qemu/qemu)
[![LobsterOS](https://img.shields.io/badge/runs-LobsterOS-brightgreen.svg)](https://github.com/jlaportebot/lobster-os)

> **A complete QEMU-based Raspberry Pi 5 (BCM2712) emulator with custom MMIO devices for networking and input, enabling full LobsterOS bare-metal development and testing without physical hardware.**

---

## 🎬 Acceptance Demo

**Watch LobsterOS running on this emulator (1:02):**

https://github.com/jlaportebot/pi5-emulator/blob/main/acceptance_demo.mp4

*The same demonstration video from LobsterOS — recorded entirely on this QEMU emulator. Shows: bare-metal boot → shell → C compilation → editor → Python → network → packages → GitHub CLI.*

---

## ✨ What Makes This Special

This is **not** a generic QEMU build. It includes two custom MMIO devices that bridge the emulator to the host:

| Device | Address | Purpose |
|--------|---------|---------|
| **lobster-net** | `0x7D00_9000` | HTTP/HTTPS proxy — the guest writes a URL to the device, the *host* performs the TLS connection, returns response to guest. Enables `wget`, `curl`, `pkg`, `gh` in the VM. |
| **lobster-input** | `0x7D00_8000` | Keyboard/mouse passthrough — host input events forwarded to guest via simple MMIO FIFO. Enables GUI interaction without USB/XHCI emulation complexity. |

This means LobsterOS gets **real internet access** and **real input** while running in a bare-metal VM — no user-mode networking, no SLIRP, no virtio-net complexity.

---

## 🏗 Architecture

```
pi5-emulator/
├── hw/
│   ├── arm/
│   │   ├── bcm2712.c              # BCM2712 SoC initialization (memory map, GIC, CPU)
│   │   ├── bcm2712_peripherals.c  # Peripherals container with alias mapping
│   │   ├── bcm2712_rp1.c          # RP1 I/O controller (GPIO, UART, I2C, SPI, SD)
│   │   ├── bcm2712_v3d.c          # V3D GPU placeholder (mailbox channel only)
│   │   ├── bcm2835_cprman.c       # CPRMAN clock manager (UART 3MHz from XOSC)
│   │   ├── bcm2835_fb.c           # Framebuffer (V3D mailbox channel alias technique)
│   │   ├── bcm2835_peripherals.c  # Base peripherals (IC, timer, DMA, USB, etc.)
│   │   ├── bcm2836.c              # Pi 3/4 support (backward compat)
│   │   ├── raspi.c                # Base Raspberry Pi machine
│   │   ├── raspi5b.c              # Pi 5 machine definition (raspi5b)
│   │   └── bcm_pcie.c             # PCIe controller (disabled - compilation issues)
│   ├── display/
│   │   └── bcm2835_fb.c           # Framebuffer (modified for V3D alias)
│   ├── misc/
│   │   └── bcm2835_cprman.c       # CPRMAN clock manager
│   └── pci-host/
│       └── bcm_pcie.c             # PCIe host controller (disabled)
├── include/
│   ├── hw/arm/
│   │   ├── bcm2712.h
│   │   ├── bcm2712_peripherals.h
│   │   ├── bcm2712_rp1.h
│   │   ├── bcm2712_v3d.h
│   │   └── bcm2835_fb.h
│   └── hw/display/
│       └── bcm2835_fb.h
├── bcm2712-rpi-5-b.dtb            # Pi 5 device tree (Linux-compatible)
├── Image-pi5                       # Raspberry Pi OS kernel (for Linux boot testing)
├── initramfs8                      # Raspberry Pi OS initramfs
├── kernel8.img                     # LobsterOS kernel (built from lobster-os repo)
├── run_lobsteros_shell.sh          # Headless serial console launcher
├── run_lobsteros_gui.sh            # GTK graphical display launcher
├── lobsteros_auto_demo.py          # Automated demo driver (serial scripting)
├── lobsteros_gui.py                # Serial→framebuffer mirror for live terminal
├── STATUS.md                       # Detailed implementation status
└── README.md
```

---

## 📦 Quick Start

### Prerequisites

**Ubuntu/Debian:**
```bash
sudo apt-get update && sudo apt-get install -y \
    git ninja-build pkg-config libglib2.0-dev libpixman-1-dev \
    libfdt-dev libslirp-dev libcapstone-dev libseccomp-dev \
    python3 python3-venv python3-pip \
    libgtk-3-dev  # for graphical display
```

**Raspberry Pi OS / ARM64 host:** Same packages, works natively.

### Build QEMU (with lobster-net + lobster-input)

```bash
# Clone this repo (contains only Pi 5 patches, not full QEMU)
git clone https://github.com/jlaportebot/pi5-emulator.git
cd pi5-emulator

# Build QEMU from source with our patches applied
# (The configure script patches QEMU's hw/arm/ with our files)
./configure --target-list=aarch64-softmmu --enable-debug --enable-gtk
ninja -C build

# Verify build
./build/qemu-system-aarch64 -version
# Should show: QEMU emulator version 9.x.x (with raspi5b machine)
```

### Run LobsterOS (Headless Serial Console)

```bash
# Requires LobsterOS kernel built from lobster-os repo
# kernel8.img should be at /mnt/data/sd-overflow/LobsterOS/lobster-os/build/kernel8.img
# (or copy to ./kernel8.img)

./run_lobsteros_shell.sh

# In the shell:
#   help              # 100+ commands
#   version           # LobsterOS v0.2.0
#   cc hello.c -run   # Compile C → AArch64 ELF → run
#   run /bin/python   # MicroPython REPL
#   wget example.com  # HTTP via lobster-net
#   gh auth login     # GitHub CLI
```

### Run LobsterOS (Graphical GTK Display)

```bash
./run_lobsteros_gui.sh

# Opens a GTK window with framebuffer display
# Serial console still available in terminal
# Mouse/keyboard work via lobster-input MMIO device
```

### Run Raspberry Pi OS (Linux)

```bash
./build/qemu-system-aarch64 -M raspi5b -m 4G -cpu cortex-a76 -smp 4 \
    -kernel Image-pi5 -initrd initramfs8 -dtb bcm2712-rpi-5-b.dtb \
    -serial stdio -display none \
    -append "console=ttyAMA0,115200 root=/dev/ram0 rw"
```

---

## 🔧 Custom MMIO Devices (The Magic)

### lobster-net (Network Proxy)

**Register Map** (base `0x7D00_9000`):
| Offset | Name | Access | Description |
|--------|------|--------|-------------|
| `0x00` | VERSION | R | 0x00030000 (v1.2) |
| `0x04` | CMD | R/W | Command register |
| `0x08` | STATUS | R | Status/result |
| `0x0C` | URL_LEN | R/W | URL length |
| `0x10` | URL_BUF | W | URL buffer (3840 bytes max) |
| `0x14` | AUTH_MODE | R/W | 0=none, 1=Bearer (write token + `\n` + URL) |
| `0x18` | RESP_LEN | R | Response length |
| `0x20` | RESP_BUF | R | Response buffer (512 KB max) |

**Protocol v1.2 (Bearer Auth)**:
```c
// Guest writes:
*(volatile u32*)(NET_BASE + 0x14) = 1;                    // AUTH_MODE = Bearer
write_string(NET_BASE + 0x10, "ghp_xxx\nhttps://api.github.com/user");
*(volatile u32*)(NET_BASE + 0x04) = CMD_FETCH;            // CMD = 1

// Host (QEMU) performs HTTPS request with Authorization: Bearer ghp_xxx
// Returns response to RESP_BUF, sets RESP_LEN, STATUS = 0
```

**Used by**: `wget`, `curl`, `pkg update`, `gh api`, `gh repo list`, `gh auth login`

### lobster-input (Keyboard/Mouse)

**Register Map** (base `0x7D00_8000`):
| Offset | Name | Access | Description |
|--------|------|--------|-------------|
| `0x00` | VERSION | R | 0x00010000 |
| `0x04` | KEY_FIFO_HEAD | R | Ring buffer head |
| `0x08` | KEY_FIFO_TAIL | R | Ring buffer tail |
| `0x10` | KEY_FIFO_DATA | R | Key events (u32: type<<24 \| code<<16 \| value) |
| `0x20` | MOUSE_X | R | Relative X |
| `0x24` | MOUSE_Y | R | Relative Y |
| `0x28` | MOUSE_BTN | R | Button mask |

**Event Types**: `1=key_down`, `2=key_up`, `3=mouse_move`, `4=mouse_btn`

**Used by**: Window manager input, GUI apps, GTK display

---

## 📊 Implementation Status (v1.2)

| Component | Status | Notes |
|-----------|--------|-------|
| **BCM2712 SoC** | ✅ Complete | 4× Cortex-A76, GIC-400, memory map |
| **Memory System** | ✅ Complete | 4 GB RAM, 32-bit alias (0x7C00_0000), SCB bus (0x1_07C0_0000) |
| **Framebuffer** | ✅ Complete | V3D mailbox channel alias, 1920×1080×32 |
| **Peripherals** | ✅ Complete | OTP, Property Channel, SDHCI (EMMC1), SDHOST, DMA, DWC2 USB, Power, SPI, I2C×3, UART0 (dual addr), EMMC2, RP1 I/O |
| **GIC-400** | ✅ Complete | 4 cores, 512 SPI, virtualization, ITS |
| **BCM2835 IC** | ✅ Complete | Legacy GPU/ARM IRQ (64+8) |
| **CPRMAN** | ✅ Complete | UART clock (3 MHz from XOSC) |
| **DMA Remap** | ✅ Complete | 0xC000_0000 → 0x0000_0000 (30-bit) |
| **lobster-net MMIO** | ✅ Complete | v1.2, Bearer auth, 3840 URL / 512 KB resp |
| **lobster-input MMIO** | ✅ Complete | Keyboard + mouse FIFO |
| **GENET Ethernet** | ❌ Disabled | qdev_realize parent_bus conflict |
| **XHCI USB 3.0** | ❌ Disabled | — |
| **PCIe** | ❌ Disabled | BCM PCIe driver compilation issues |
| **HDMI** | ❌ Disabled | — |
| **Audio** | ❌ Disabled | — |
| **V3D GPU** | ⚠️ Placeholder | Mailbox channel only |

### Known Issues

1. **GIC Duplicate Property Warnings** — Non-fatal ERROR logs during GIC realize (`gic_cpu[0]`, `gic_viface[0]` duplicates). Does not affect functionality.
2. **Linux Kernel Boot** — Pi OS kernel produces no console output (GIC/device tree mismatches). LobsterOS boots perfectly.
3. **Missing DT Bindings** — Many Pi 5 specific device tree bindings not implemented.

---

## 🚀 Usage Examples

### Automated Demo (CI/Testing)

```bash
# Run the automated demo driver (scripts commands over serial)
python3 lobsteros_auto_demo.py

# Or use the shell script with embedded commands
./run_lobsteros_shell.sh << 'EOF'
help
version
cc /bin/hello.c -run
run demo
run /bin/python
2+2
exit()
halt
EOF
```

### Custom Kernel

```bash
# Build your own LobsterOS kernel
cd ../lobster-os
cargo build --features qemu_raspi5b --target aarch64-unknown-none --release
cp target/aarch64-unknown-none/release/lobster-os ../pi5-emulator/kernel8.img

# Run
./run_lobsteros_shell.sh
```

### Debugging with GDB

```bash
# QEMU with GDB stub
./build/qemu-system-aarch64 -M raspi5b -m 4G -cpu cortex-a76 -smp 4 \
    -kernel kernel8.img -serial stdio -display none -s -S

# Connect GDB
aarch64-none-elf-gdb kernel8.img
(gdb) target remote :1234
(gdb) break kmain
(gdb) continue
```

---

## 🔗 Related Repositories

| Repo | Description |
|------|-------------|
| [lobster-os](https://github.com/jlaportebot/lobster-os) | The bare-metal Rust OS that runs on this emulator |
| [pi5-emulator](https://github.com/jlaportebot/pi5-emulator) | This repository |

---

## 📋 Changelog

### v1.2 (2026-09-07) — Current
- **lobster-net v1.2**: Bearer token authentication mode, increased buffers (URL 3840B, Response 512KB)
- **lobster-input sync**: Fixed key/mouse event forwarding for GTK display
- **kernel8.img updated**: LobsterOS v0.2.0 with full shell, editor, Python, network, packages, gh
- **Launcher scripts**: `run_lobsteros_shell.sh` (headless) + `run_lobsteros_gui.sh` (GTK)
- **Device tree**: Updated `bcm2712-rpi-5-b.dtb` for Pi 5

### v1.1 (2026-08-30)
- Fixed GUI launcher: direct QEMU GTK (removed Tkinter wrapper)
- Added resizable GTK display + serial-to-framebuffer mirror (`lobsteros_gui.py`)
- Added headless shell launcher + desktop entries

### v1.0 (2026-06-21)
- Initial Pi 5 (BCM2712) QEMU emulator
- Complete device realization without crashes
- Basic QEMU patches for BCM2712, RP1, V3D framebuffer

---

## 📄 License

MIT License — see [LICENSE](LICENSE) file for details.

---

## 🤝 Contributing

1. Fork the repo
2. Apply changes to `hw/arm/*.c` and `include/hw/arm/*.h`
3. Test with `./run_lobsteros_shell.sh` and LobsterOS
4. Submit PR with description of what device/feature was added/fixed

**Good first issues:** GIC duplicate properties, GENET Ethernet, PCIe compilation, DT bindings.

---

## 🙏 Acknowledgments

- QEMU project (upstream emulation framework)
- Raspberry Pi documentation (BCM2712, RP1 datasheets)
- Linux kernel source (device tree bindings reference)
- LobsterOS project (the OS that makes this emulator useful)

---

**Pi 5 Emulator** — Built with 🦞 by Mister Lobster