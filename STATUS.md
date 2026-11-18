# Status

Facts only, as verified on 2026-11-17 on the Pi 5 host with the QEMU binary
`build-opt/qemu-system-aarch64` and kernel build `aa9d2c8-kernel8.img`.
Environment for every command below:

```bash
export LOBSTER_BASE_DISK=<scratch>/base.img      # python3 tools/mkbase.py --output $LOBSTER_BASE_DISK --force
export LOBSTER_KERNEL=<campaign>/kernels/aa9d2c8-kernel8.img
export LOBSTERVM_HOME=<scratch>/vmhome LOBSTERVM_RUNTIME=<scratch>/vmrun
```

## Works, and how it was verified

| Capability | Command | Result |
|------------|---------|--------|
| Environment check | `./lobstervm.sh doctor` | kernel, device tree, base disk reported `ok` |
| Base disk build | `python3 tools/mkbase.py --output $LOBSTER_BASE_DISK --force` | 67108864 bytes, fsck clean |
| Base disk reproducible | `python3 tools/mkbase.py --output $LOBSTER_BASE_DISK --check` | `OK: built image matches ...` |
| Create, boot, wait | `./lobstervm.sh create demo`; `./lobstervm.sh start demo --headless --wait` | `Desktop ready (guest boot ~1000-1400 ms)` |
| Guest shell | `./lobstervm.sh shell demo uname` | prints kernel, architecture and CPU lines |
| Screenshot | `./lobstervm.sh screenshot demo shot.png` | PNG written |
| Resolution | `./lobstervm.sh create fu1 --resolution 1280x720 ...` then `screenshot` | PNG header reads 1280 x 720 (default is 1920x1080) |
| Shared folder | `create fu1 --share DIR` with `proof.txt` in DIR; `shell fu1 'ls /share'`, `shell fu1 'cat /share/proof.txt'` | prints `proof.txt` and `hello-share` |
| Device properties | `ps` on the running VM | command line contains `-global lobster-sys.width=1280 -global lobster-sys.height=720 -global lobster-share.dir=...` and no `LBST_*` env is passed |
| Snapshots | `snapshot demo take clean`, `list`, `restore clean`, `delete clean` | each exits 0 and prints the tag |
| Resume snapshot | `start demo --headless --resume clean`, then `shell demo version` | guest answers |
| Disk info/export/import | `disk info demo`, `disk export demo out.img`, `disk import demo out.img --yes` | exit 0 |
| Disk resize, never-booted VM | `disk resize rs 128M --yes` | `Disk of rs resized to 128.0 MiB` |
| Factory reset | `factory-reset demo` | `Disk of demo reset to the pristine system image` |
| Install/uninstall | `HOME=<scratch>/fakehome bash install.sh`, then `lobstervm list`, then `bash uninstall.sh` | links, desktop entry and icon created and removed |

## Tests

- Unit tests: `python3 -m unittest discover -s tests` -> `Ran 172 tests`, `OK`
  (test_disk 37, test_gui_harness_helpers 8, test_gui_helpers 30, test_lobstervm 74,
  test_machine_command 6, test_mkbase 17; counts of `def test_` methods).
- Lint: `ruff check lobstervm/ tools/ tests/` (ruff 0.15.15) -> `All checks passed!`
- `bash tools/check.sh` -> exit 0.
- GUI harness: `python3 tools/gui_test_vm.py --kernel $LOBSTER_KERNEL` -> `12/12 tests passed`,
  run twice in a row with identical results. Tests: el0_app, start_menu,
  start_menu_esc, clock, apps, bg_app, python, pkg_install, resolution, calc_keys,
  ctrl_c, welcome_once. `start_menu_esc` reports `[XFAIL]` (see known issues) and
  counts as passed.

## Known issues

1. Esc does not close the start menu. The harness prints `[XFAIL]`; it prints
   `[XPASS]` when a kernel fixes this. The start menu test closes it with a second
   click on the start button instead.
2. The Welcome window is not painted on first boot (reported against this kernel build;
   the harness checks the serial log marker and `wins` output, not the pixels, and passes).
3. `dmesg` prints nothing: `./lobstervm.sh shell dm dmesg` returned an empty line.
4. Shift via the QEMU monitor is read as Ctrl by the guest: `lkey 0x06 shift` types
   `5`, while `lkey 0x06 ctrl` types `%`. QEMU's input flags (bit0 shift, bit1 ctrl)
   do not match the bit layout the kernel's `hid::shift_held` tests (bit1 = left shift).
   The harness sends `ctrl` for `%` and will fail when the kernel is fixed; switch
   `PERCENT_MODS` in `tools/gui_test_vm.py` to `shift` then. The calculator's `%` is modulo:
   `5 % 0 =` shows `Error`.
5. `disk resize` on a VM that has been booted fails with `Filesystem still has errors`
   from `e2fsck` (exit 1), even after a clean `stop`; it works on a never-booted VM.
6. `start --headless --resume TAG --wait` times out after 90 s waiting for the
   `desktop-ready` marker, which a restored snapshot does not print again. Start
   without `--wait`.
7. `lobstervm gui` was not run: this host session has no display (`tkinter` imports).
8. Shell command separators are not supported in the guest shell: `ls /share; cat x`
   is parsed as one command. Run each command separately.
9. The Linux (Raspberry Pi OS) kernel does not produce console output on this machine
   model; LobsterOS is the supported guest. (Carried over from earlier status; not re-run.)

## Not verified in this pass

- Building the QEMU binary from source (configure flags are copied from the existing
  build tree's `config.status`).
- GTK, SDL and VNC displays; audio on.
- The graphical manager.
