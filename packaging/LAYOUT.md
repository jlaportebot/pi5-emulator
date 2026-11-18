# Installed bundle layout

`lobstervm/paths.py` looks for components in this order: environment
variable, installed bundle (only when running frozen), development checkout.

## Windows and Linux (PyInstaller onedir)

```
LobsterVM/
  LobsterVM[.exe]            windowed GUI
  lobstervm-cli[.exe]        console CLI (create/start/shell/screenshot/...)
  _internal/                 PyInstaller runtime
  qemu/bin/qemu-system-aarch64[.exe]
  qemu/bin/qemu-img[.exe]    (+ the DLLs/libs of the lobster-qemu archive)
  share/lobsteros/kernel8.img
  share/lobsteros/bcm2712-rpi-5-b.dtb
  share/lobsteros/lobsteros.img
```

The directory searched is the one holding `sys.executable`.

## macOS (`LobsterVM.app`)

```
LobsterVM.app/Contents/
  MacOS/LobsterVM, MacOS/lobstervm-cli
  Resources/qemu/bin/...     same files as above
  Resources/share/lobsteros/...
```

Searched, in order: `Contents/MacOS`, `Contents/Resources`, `Contents`.
Components live in `Contents/Resources` (code signing treats it as data).

## Environment overrides (always win)

`LOBSTER_QEMU`, `LOBSTER_QEMU_IMG`, `LOBSTER_KERNEL`, `LOBSTER_DTB`,
`LOBSTER_BASE_DISK`, `LOBSTERVM_HOME`, `LOBSTERVM_RUNTIME`.

## Base image

The installed `lobsteros.img` is read-only and may move on update. On the
first VM creation it is copied to `<data dir>/base/lobsteros-<sha256-prefix>.img`
(reused when present) and every VM overlay is backed by that copy.

## Data directories

| OS      | VMs                                          | Runtime (sockets, pids)             |
|---------|----------------------------------------------|-------------------------------------|
| Linux   | `$XDG_DATA_HOME/lobstervm`                   | `$XDG_RUNTIME_DIR/lobstervm`        |
| macOS   | `~/Library/Application Support/LobsterVM`    | `$TMPDIR/lobstervm-<uid>`           |
| Windows | `%LOCALAPPDATA%\LobsterVM`                   | `%LOCALAPPDATA%\LobsterVM\run`      |

Windows (and `LOBSTERVM_FORCE_TCP=1`) use loopback TCP ports recorded in
`<runtime>/<vm>/{monitor,serial}.port` instead of Unix sockets.
