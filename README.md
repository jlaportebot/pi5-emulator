# LobsterVM — VirtualBox-style VM Manager for LobsterOS Pi 5 Emulator

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![QEMU](https://img.shields.io/badge/QEMU-9.0+-orange.svg)](https://www.qemu.org/)

**LobsterVM** is a pure-Python VM manager (à la VirtualBox) for the Raspberry Pi 5 (BCM2712) QEMU emulator. It handles persistent VM definitions, qcow2 disk layering, snapshots, and a full **tkinter graphical manager** — no external dependencies beyond the standard library.

The emulator itself lives in the companion QEMU fork (providing `raspi5b` machine, `lobster-net` and `lobster-input` MMIO devices). This repo provides the management layer.

---

## 📦 Quick Start

### Prerequisites

**Debian/Ubuntu/Raspberry Pi OS:**
```bash
sudo -S -p '' apt-get update && sudo -S -p '' apt-get install -y \
    qemu-system-arm qemu-utils \
    python3 python3-tk \
    libgtk-3-dev  # for QEMU GTK display
```

You also need the LobsterOS QEMU fork binary at `../qemu-src/build-opt/qemu-system-aarch64` (or set `LOBSTER_QEMU`).

### Install (Desktop Integration)

```bash
./install.sh
```

This:
- Symlinks `lobstervm.sh` → `~/.local/bin/lobstervm` (add to PATH)
- Installs `lobstervm-gui.desktop` → `~/.local/share/applications/`
- Installs icon → `~/.local/share/icons/hicolor/256x256/apps/lobstervm.png`

After install, run `lobstervm` from any terminal or find **"LobsterVM Manager"** in your application menu.

### Uninstall

```bash
./uninstall.sh
```

---

## 🖥 Commands

All commands via `./lobstervm.sh` (or `lobstervm` after install):

| Command | Description |
|---------|-------------|
| `list` | List VMs and their state |
| `doctor` | Check QEMU, kernel, DTB, base disk |
| `gui` | **Open graphical VM manager (E2.3)** |
| `create` | Create a new VM |
| `modify` | Change a VM's settings |
| `show` | Show a VM's settings and state |
| `delete` | Delete a VM and its disk |
| `start` | Boot a VM (`--headless`, `--display`, `--resume TAG`, `--wait`) |
| `stop` | Shut down cleanly (`--force` for power-off) |
| `reset` | Hard-reset the VM |
| `pause` / `resume` | Freeze / unfreeze the VM |
| `factory-reset` | Discard all guest disk changes |
| `snapshot` | Take/restore/list/delete snapshots |
| `screenshot` | Save the guest screen to PNG/PPM |
| `click` / `move` / `drag` | Inject mouse input |
| `key` / `type` | Inject keyboard input |
| `shell` | Run a guest shell command, print output |
| `hmp` | Run a raw QEMU monitor command |
| `serial` | Show the guest serial log |

### Examples

```bash
# Create a VM
lobstervm create myvm --memory 4 --resolution 1920x1080 --display gtk

# Start it graphically
lobstervm start myvm

# Take a snapshot
lobstervm snapshot myvm take "clean-boot"

# Restore later
lobstervm snapshot myvm restore "clean-boot"

# Open the GUI manager
lobstervm gui
```

---

## 🖱 Graphical Manager (`lobstervm gui`)

The **tkinter-based GUI** (roadmap E2.3) provides:

- **VM List** (left): name, state (running/stopped/paused), memory, resolution; refresh button + 3s auto-refresh
- **Details Pane** (right): disk path, memory, resolution, display, audio, shared folder, kernel, description, status, uptime
- **Actions**: Start, Stop, Pause/Resume, Reset, Screenshot, Settings, Snapshots
- **New VM Dialog**: name, memory GB, resolution, disk size — validates via `config.py`
- **Settings Dialog**: modify any VM field — reuses CLI's modify path
- **Snapshot Tree**: list/create/restore/delete via `Machine.snapshot_*`
- **Live Serial Console**: tails the VM's `serial.log`, auto-refreshing
- **Screenshot**: calls `Machine.screenshot()`, displays PNG in popup
- **Thread-safe**: long operations run in background threads, UI never freezes
- **Robust**: errors show dialogs/status messages, never crash the app

### Screenshot
![GUI Manager](assets/gui-screenshot.png)  <!-- placeholder -->

---

## 🧪 Testing

```bash
# Run unit tests (headless, no display needed)
./tools/check.sh

# Or directly:
python3 -m unittest discover -s tests -v

# Live GUI regression suite (boots a throwaway VM; needs QEMU fork, kernel, base image)
python3 tools/gui_test_vm.py [--test NAME] [--junit PATH] [--output-dir DIR]
```

`tools/gui_test.py` is a thin forwarder to `gui_test_vm.py`. The suite checks
the clean-boot desktop (no app windows, one-time Welcome), launches apps with
the guest `app <name>` command and saves screenshots to `--output-dir`.

Tests cover:
- `format_vm_row()` — VM list formatting
- `serial_tail()` — log tailing logic
- `validate_new_vm_params()` — new-VM dialog validation (delegates to `config.py`)
- `VmConfig` validation — all error paths
- `VmStore` — create/list/load/delete/snapshots

---

## 🏗 Architecture

```
lobstervm/
├── __init__.py
├── cli.py          # argparse front end
├── config.py       # VmConfig, VmStore, validation, qcow2 layering
├── machine.py      # Machine: start/stop/pause/resume/reset, HMP, serial, snapshots, screenshots
├── paths.py        # Path resolution (no hard-coded paths)
└── gui.py          # tkinter graphical manager (E2.3)
```

**Disk layering**: Each VM gets a qcow2 overlay on the pristine base image (`lobsteros.img`). The base is never written; all guest writes and snapshots land in the per-VM overlay. Build the base once with `python3 tools/mkbase.py` (it is not tracked; `--check` proves it matches `base-root/`).

**Disk tools**: `lobstervm disk info|export|import|resize <vm> ...` (stopped VM for all but `info`). `resize` grows the ext2 volume in place (max 1G: the guest driver handles 128 block groups) and discards snapshots.

**Snapshots**: Stored inside the VM's qcow2 disk (`qemu-img snapshot`). Supports multiple named tags.

---

## 📁 Repository Layout

```
├── lobstervm/              # Python package (the manager)
│   ├── cli.py
│   ├── config.py
│   ├── machine.py
│   ├── paths.py
│   └── gui.py
├── tests/                  # Unit tests (stdlib unittest)
│   └── test_gui_helpers.py
├── tools/
│   ├── check.sh            # Test runner
│   ├── gui_test_vm.py      # Live GUI regression suite (lobstervm Machine API)
│   ├── gui_test.py         # Forwards to gui_test_vm.py
│   └── mkbase.py           # Builds lobsteros.img from base-root/
├── lobstervm.sh            # Entry point (./lobstervm.sh <command>)
├── install.sh              # Desktop integration installer
├── uninstall.sh            # Desktop integration uninstaller
├── lobstervm-gui.desktop   # .desktop launcher
├── assets/
│   └── lobsteros-shell-icon.png
├── kernel8.img             # LobsterOS kernel (build artifact)
├── base-root/              # File tree of the base disk (tracked)
├── base-root.manifest      # Modes/owners for base-root/
├── lobsteros.img           # Base disk image, built by tools/mkbase.py (not tracked)
├── bcm2712-rpi-5-b.dtb     # Pi 5 device tree
├── Image-pi5               # Raspberry Pi OS kernel (for Linux boot testing)
├── initramfs8              # Raspberry Pi OS initramfs
├── README.md               # This file
├── STATUS.md               # Implementation status
└── release_notes.md        # Changelog
```

---

## 🔧 Configuration

Environment variables (all optional):

| Variable | Purpose |
|----------|---------|
| `LOBSTER_QEMU` | Path to `qemu-system-aarch64` with `raspi5b` machine |
| `LOBSTER_KERNEL` | Override default `kernel8.img` |
| `LOBSTER_DTB` | Override default DTB |
| `LOBSTER_BASE_DISK` | Override base disk image |
| `LOBSTERVM_HOME` | VM store directory (default: `~/.local/share/lobstervm`) |
| `LOBSTERVM_RUNTIME` | Runtime dir for sockets/pids (default: `$XDG_RUNTIME_DIR/lobstervm`) |

---

## 🤝 Contributing

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Write tests first (TDD)
4. Implement, verify `./tools/check.sh` passes
5. Submit PR with description of changes

---

## 📄 License

MIT License — see [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- **QEMU Project** — upstream emulation framework
- **Raspberry Pi Documentation** — BCM2712, RP1 datasheets
- **LobsterOS** — the bare-metal Rust OS that makes this emulator useful

---

**LobsterVM** — Built with 🦞 by the LobsterOS team
