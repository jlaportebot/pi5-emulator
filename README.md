# LobsterVM

LobsterVM is a VirtualBox-style VM manager plus a QEMU machine model for running
LobsterOS, a Rust desktop OS for the Raspberry Pi 5, on a Pi 5 or on any
aarch64 or x86_64 Linux host. The manager (`lobstervm`) is pure Python (standard
library only; the optional GUI needs `python3-tk`). The machine (`raspi5b`, the
BCM2712 SoC plus the `lobster-*` devices) lives in a QEMU fork.

- Kernel (guest OS): https://github.com/jlaportebot/lobster-os
- QEMU fork: https://github.com/jlaportebot/lobster-qemu, branch `lobster/main`
- Current verification state and known issues: [STATUS.md](STATUS.md)

## Install

```bash
bash install.sh
```

This links `lobstervm.sh` to `~/.local/bin/lobstervm`, copies
`lobstervm-gui.desktop` to `~/.local/share/applications/` and the icon to
`~/.local/share/icons/hicolor/256x256/apps/lobstervm.png`. Make sure
`~/.local/bin` is on `PATH`. Remove it again with `bash uninstall.sh`.
Without installing, use `./lobstervm.sh` from the repository root.

Requirements: Python 3.10+, `qemu-utils` (`qemu-img`), `e2fsprogs`, Pillow for
the GUI harness, and the QEMU fork binary (see "Building the QEMU binary").
`lobstervm doctor` checks that the kernel, device tree and base disk are found.

## Quick start

```bash
lobstervm doctor
python3 tools/mkbase.py --output lobsteros.img        # once: build the base disk
lobstervm create myvm --memory 4 --resolution 1280x720
lobstervm start myvm --headless --wait                # returns when the desktop is up
lobstervm shell myvm uname                            # run a guest shell command
lobstervm screenshot myvm shot.png
lobstervm stop myvm
lobstervm gui                                         # graphical manager (needs a display)
```

`lobstervm start myvm` without `--headless` opens a GTK window; `--display`
selects `gtk`, `sdl`, `vnc` or `none`. Other commands: `list`, `show`, `modify`,
`delete`, `reset`, `pause`, `resume`, `factory-reset`, `click`, `move`, `drag`,
`key`, `type`, `hmp`, `serial`. `lobstervm <command> --help` lists the options.

## VM settings

Set with `create` and changed with `modify` (VM stopped).

| Setting | Option | Values |
|---------|--------|--------|
| Memory | `--memory` | 2, 4, 8 or 16 (GB); maps to a QEMU machine variant |
| Resolution | `--resolution` | `WIDTHxHEIGHT`, default `1920x1080` |
| Shared folder | `--share DIR` | host directory, visible in the guest as `/share` |
| Audio | `--audio on\|off` | headless VMs always run with `-audio none` |
| Display | `--display` | `gtk`, `sdl`, `vnc`, `none` |
| Kernel | `--kernel PATH` | `kernel8.img` to boot |

Resolution and shared folder reach the guest through device properties on the
command line, for example:

```
qemu-system-aarch64 -M raspi5b,graphics=on -m 4G -cpu cortex-a76 -kernel kernel8.img \
  -dtb bcm2712-rpi-5-b.dtb -drive if=none,id=lobsterdisk,file=disk.qcow2,format=qcow2 \
  -serial stdio -monitor unix:monitor.sock,server,nowait -display none \
  -global lobster-sys.width=1280 -global lobster-sys.height=720 \
  -global lobster-share.dir=/path/to/share -audio none
```

Commas in the shared folder path are doubled for QEMU. Example check of a
shared folder: put `proof.txt` in the folder, then `lobstervm shell myvm 'ls /share'`.

## Disks

Each VM has a qcow2 overlay on a read-only base image (`lobsteros.img`, or the
path in `LOBSTER_BASE_DISK`). Build the base from `base-root/` and
`base-root.manifest`:

```bash
python3 tools/mkbase.py --output lobsteros.img
python3 tools/mkbase.py --output lobsteros.img --check   # compare with a fresh build
```

```bash
lobstervm disk info myvm
lobstervm disk export myvm out.img            # standalone flattened copy (VM stopped)
lobstervm disk import myvm out.img --yes      # replace the disk; data and snapshots are lost
lobstervm disk resize myvm 128M --yes         # grow the ext2 volume (VM stopped, snapshots lost)
lobstervm factory-reset myvm                  # discard all guest disk changes
```

`resize` has a known failure after a VM has been booted; see STATUS.md.

## Snapshots

Snapshots live inside the VM's qcow2 disk.

```bash
lobstervm snapshot myvm take clean
lobstervm snapshot myvm list
lobstervm snapshot myvm restore clean
lobstervm snapshot myvm delete clean
lobstervm start myvm --headless --resume clean   # do not add --wait: see STATUS.md
```

## Tests

```bash
bash tools/check.sh                              # unit tests, syntax and CLI smoke checks
python3 -m unittest discover -s tests            # unit tests only (needs LOBSTER_BASE_DISK)
python3 tools/gui_test_vm.py --kernel kernel8.img   # live GUI harness
```

`tools/gui_test_vm.py` boots a throwaway VM and drives it through the QEMU
monitor and the serial console, asserting on screenshot pixels and serial
output. It covers: EL0 app window, start menu (open, close by click, Esc as an
expected failure), panel clock, app launching, background app, MicroPython,
`pkg install`, runtime resolution change, calculator keyboard input with
`5 % 0` (display must read `Error`, no panic), Ctrl+C, and the one-time Welcome
window across two boots. It prints `[XFAIL]` for the Esc check on kernels where
Esc does not close the menu and `[XPASS]` when that starts working. Unit tests
need `LOBSTER_BASE_DISK` pointing at a built base image.

## Building the QEMU binary

The binary is looked up at `../qemu-src/build-opt/qemu-system-aarch64`, or set
`LOBSTER_QEMU`. The existing `build-opt` tree was configured with:

```bash
git clone -b lobster/main https://github.com/jlaportebot/lobster-qemu.git qemu-src
mkdir -p qemu-src/build-opt && cd qemu-src/build-opt
../configure --target-list=aarch64-softmmu --enable-lto -Doptimization=3 -Db_ndebug=true
ninja
```

(Flags are copied from that tree's `config.status`; the clone-and-build
sequence itself was not re-run when this file was written.)

## Environment variables

| Variable | Purpose |
|----------|---------|
| `LOBSTER_QEMU` | Path to `qemu-system-aarch64` with the `raspi5b` machine |
| `LOBSTER_KERNEL` | Kernel to boot instead of the bundled one |
| `LOBSTER_DTB` | Device tree override |
| `LOBSTER_BASE_DISK` | Base disk image override |
| `LOBSTERVM_HOME` | VM store (default `~/.local/share/lobstervm`) |
| `LOBSTERVM_RUNTIME` | Sockets and pid files (default `$XDG_RUNTIME_DIR/lobstervm`) |

## Layout

```
lobstervm/        manager package: cli.py, config.py, machine.py, disk.py, paths.py, gui.py
tests/            unittest suite
tools/            check.sh, gui_test_vm.py, mkbase.py, serial_shell.py, ...
base-root/        file tree of the base disk, with base-root.manifest for modes/owners
lobstervm.sh      entry point
install.sh        desktop integration (uninstall.sh reverses it)
STATUS.md         verified state and known issues
```
