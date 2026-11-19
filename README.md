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

### One-click installers (recommended)

Download from the [latest release](https://github.com/jlaportebot/pi5-emulator/releases/latest)
(rolling builds: the `nightly` prerelease). Each installer bundles QEMU, the
LobsterOS kernel, the device tree and a base disk; nothing else is needed.
Verify downloads against `SHA256SUMS`.

| OS | File | Install |
| --- | --- | --- |
| Windows (x64) | `LobsterVM-Setup-<ver>-x64.exe` | Run it (per-user, no admin). Start menu entry, optional desktop shortcut, uninstaller in Settings > Apps. |
| macOS (Apple Silicon) | `LobsterVM-<ver>-arm64.dmg` | Open the dmg, drag LobsterVM to Applications. |
| Linux (x86_64, aarch64) | `LobsterVM-<ver>-<arch>.AppImage` | `chmod +x` the file and run it. |

First run: launch LobsterVM. On first launch with no VMs it creates a default
VM named "LobsterOS" from the bundled disk; click Start. The CLI is reachable
by passing arguments to the same binary (for example
`./LobsterVM-<ver>-x86_64.AppImage create demo`, or `lobstervm.exe` in the
Windows install folder).

The builds are not code-signed (QEMU runs with TCG, so no entitlements are
needed):

- Windows SmartScreen: click "More info", then "Run anyway".
- macOS Gatekeeper: right-click the app, choose "Open", then "Open" again.
- Uninstall: Windows via Settings > Apps (or the Start menu uninstaller);
  macOS by deleting LobsterVM.app (VM data is in
  `~/Library/Application Support/LobsterVM`); Linux by deleting the AppImage
  (data in `~/.local/share/lobstervm`).

### From source

```bash
bash install.sh
```

This links `lobstervm.sh` to `~/.local/bin/lobstervm`, copies
`lobstervm-gui.desktop` to `~/.local/share/applications/` and the icon to
`~/.local/share/icons/hicolor/256x256/apps/lobstervm.png`. Make sure
`~/.local/bin` is on `PATH`. Remove it again with `bash uninstall.sh`.
Without installing, use `./lobstervm.sh` from the repository root.

Requirements for the source install: Python 3.10+, `qemu-utils` (`qemu-img`), `e2fsprogs`, Pillow for
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
`delete`, `reset`, `pause`, `resume`, `factory-reset`, `click`, `move`, `wheel`,
`drag`, `key`, `type`, `hmp`, `serial`. `lobstervm <command> --help` lists the
options.

## VM settings

Set with `create` and changed with `modify` (VM stopped).

| Setting | Option | Values |
|---------|--------|--------|
| Memory | `--memory` | 2, 4, 8 or 16 (GB); maps to a QEMU machine variant |
| Resolution | `--resolution` | `WIDTHxHEIGHT`, default `1920x1080` |
| Shared folder | `--share DIR` | host directory, visible in the guest as `/share` |
| Host compiler | `--host-compile DIR` | `include/` + `liblobsterc.a` for the guest's `cc`; default off (see below) |
| Audio | `--audio on\|off` | headless VMs always run with `-audio none` |
| Display | `--display` | `gtk`, `sdl`, `vnc`, `none` |
| Kernel | `--kernel PATH` | `kernel8.img` to boot |
| Acceleration | `--accel tcg\|kvm` | `tcg` (default) or `kvm`, see below |

Resolution and shared folder reach the guest through device properties on the
command line, for example:

```
qemu-system-aarch64 -M raspi5b,graphics=on -m 4G -accel tcg -cpu cortex-a76 -kernel kernel8.img \
  -dtb bcm2712-rpi-5-b.dtb -drive if=none,id=lobsterdisk,file=disk.qcow2,format=qcow2 \
  -serial stdio -monitor unix:monitor.sock,server,nowait -display none \
  -global lobster-sys.width=1280 -global lobster-sys.height=720 \
  -global lobster-share.dir=/path/to/share -audio none
```

Commas in the shared folder path are doubled for QEMU. Example check of a
shared folder: put `proof.txt` in the folder, then `lobstervm shell myvm 'ls /share'`.

`--host-compile DIR` (on `create` and `modify`, while the VM is stopped)
switches the guest's `cc` over to the host compiler: `DIR` must hold the
headers under `include/` and the archive `liblobsterc.a` (validate rejects
anything else), and the QEMU command line gains
`-global lobster-net.compile-libc=DIR`. Empty (the default) leaves the guest's
`cc` off. This is an opt-in power feature with a security caveat: with it on, a
guest `cc` invocation runs on the host and can therefore read host files, so
point it at a directory you are happy to expose and turn it off again with
`lobstervm modify myvm --host-compile ""`. `show` reports the setting, and VMs
created before the option existed still load (the key is absent from their
`vm.json`).

`lobstervm wheel myvm DY [--dx DX]` scrolls the guest mouse wheel by `DY`
notches (`+` up, `-` down); `--dx` scrolls horizontally (`+` right, `-` left).
It is the HMP monitor's `lwheel`, so the VM must be running, and a negative
count parses without a separator: `lobstervm wheel myvm -3` scrolls down three
notches (`lobstervm wheel myvm -- -3` also works where a separator is needed).

## KVM acceleration

`tcg` (software emulation, `-accel tcg -cpu cortex-a76`) is the default, works
on every host and is what CI uses. `kvm` (`-accel kvm -cpu host`) runs the
guest on the host's own cores and boots far faster. It needs a Linux host with
an aarch64 CPU (for example a Pi 5), a readable and writable `/dev/kvm`
(usually membership of the `kvm` group), and a QEMU build with KVM support for
`raspi5b`. Enable it per VM with `lobstervm create --accel kvm` or
`lobstervm modify <vm> --accel kvm` (or the Acceleration choice in the GUI,
which is greyed out with the reason when the host cannot do it), or for one run
with `lobstervm start <vm> --accel kvm`. On an unsupported host `start` fails
with the reason; switch back with `lobstervm modify <vm> --accel tcg`.

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
window across two boots. The Esc check asserts that Esc closes the start menu
(kernels before the O3.3 start menu, master bd672c9, fail it). Unit tests
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
