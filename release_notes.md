# LobsterOS Emulator — Release 2026-10-18 (r3)

## Highlights

### Interrupt controller fixed — IRQs actually deliver for the first time
The kernel's GICv2 driver was computing distributor register offsets from the
*SPI number* instead of the full interrupt ID (SGI 0–15, PPI 16–31, SPI 32+).
Every SPI register access (enable/priority/target/config) landed on the wrong
word, so no timer, UART or network interrupt could ever be delivered — on the
emulator *or* on real hardware. The 1-bit-per-IRQ offset math now adds 32 for
SPIs. Verified live: the BCM2835 system-timer IRQ (SPI 65) now reaches the CPU
and vectors (`-trace gic_set_irq`, `-d int` show the exception taken).

### Exception state corrected
The kernel was entering EL1 with SP0 (EL1t, SPSR 0x3C4) while its exception
handlers and stack layout were written for EL1h. One-bit fix (0x3C5) plus real
handlers in the EL1t vector slots. Boot now runs the whole bring-up with a
consistent SP_EL1 bank.

### 1.76x console throughput (measured)
- QEMU rebuilt with `-O3` (stock build was a debug `-O0 -g` build): 21.8s → 14.3s
  on `seq 1 50000`.
- New **lobster-serial** bulk TX device (`0x7d00a000`, magic `LBUF`): the guest
  writes a 2048-byte payload window and one length register; the device pushes
  the whole chunk through the serial chardev in one host call.
- Kernel `fast_serial` driver probes for the device and falls back to PL011
  per-byte output automatically (same image boots on real hardware).
- `seq` batches conversions into 2KB buffers.
- End-to-end: **21.8s → 12.4s (1.76x)**, all 50,000 output lines verified,
  zero exceptions, clean boot to shell in ~2s.

### Stability
- UART RX interrupt on QEMU builds disabled in favor of the (faster) polled
  input path: taking the RX IRQ vectors into a PSTATE.IL-class UNDEF at the
  handler entry (proven by patching a NOP over the faulting instruction — it
  still faults; illegal-execution-state, not a bad encoding). Real-hardware
  builds keep the IRQ path.
- Hardware timer ticks are enabled after boot instead of during MMU setup
  (the console writer re-enables IRQs after every print, which used to let
  the first tick land mid-page-table-walk).
- Unexpected exceptions now dump ESR/SPSR/FAR/registers instead of hanging
  silently in a WFE loop.

## Artifacts
- Kernel: https://github.com/jlaportebot/lobster-os (commits fa47ea8, f77133a, cdd2a48)
- QEMU overlay: https://github.com/jlaportebot/lobster-qemu
- Launchers: run_lobsteros_shell.sh (serial), run_lobsteros_gui.sh (GTK desktop)

## r4 hotfix (later 10-18): bulk-serial shadowed lobster-net

The new lobster-serial device was mapped at 0x7d00a000 — inside lobster-net's
MMIO region (its +0x1000 response-data window). QEMU resolved the overlap in
favor of the later-mapped device, so curl/wget responses returned the serial
device's registers (zeros + `LBUF` magic) instead of the HTTP body. Moved to
0x7d100000. Verified live: `curl http://example.com` now prints the real
578-byte HTML page; seq/uptime/zero-exceptions all still pass.

## r5–r7 (2026-10-18 evening): full OS pipeline working end to end

- **Compile → run → exit → report loop**: fixed the cooperative run-and-wait
  exit path with a naked-asm resume trampoline (saves/restores x19-x30 + SP;
  `.Lcoop_resume` label). Previously the exit eret'd into the kernel caller
  with no register state restored → wild branches → silent panic-reboot
  (masked as a "fresh boot" in earlier tests). Exit codes now flow correctly:
  `cc file.c -run` reports `process exited with code N` and propagates through
  `$?` (sh semantics).
- Root-caused the recurring EC=0 UNDEF storms: `msr SP_EL1` while SP_EL1 is the
  *active* bank is architecturally UNDEFINED — fault confirmed at 0xc00b0 via
  objdump.
- **Shell redirection**: `>` and `>>` now actually work for builtins (ShellOut
  sink honoring file/pipe), with new `O_APPEND` support in the VFS (appends
  extend instead of overwrite) and a sh-style quote-aware tokenizer.
- **VFS boot layout**: fresh boots create /bin /etc /tmp /root /home /var /usr.
- **GUI verified live**: the GTK launcher boots into a graphical desktop with
  terminal, calculator, and process-manager windows on the custom BCM2712
  display stack (screenshot-verified via grim).
- **bulk-serial relocation**: lobster-serial moved to 0x7d100000 — its old
  0x7d00a000 mapping shadowed lobster-net's response window (curl responses
  were reading the serial device's registers). curl now returns real bodies.

Final acceptance (measured live, single boot, zero exceptions):
boot→shell ~2s; `cc` compiles via host cross-gcc, spawns a user process,
exit(3) reaped and reported; `$?` = 3; `free` shows 4181616 KB free;
uptime advances in real time; GUI desktop boots graphically.

## r8 (2026-10-18 night): PERSISTENT STORAGE — files survive reboots

- New **lobster-disk** paravirtual block device (QEMU, 0x7d200000): host-backed
  raw image attached via `LBST_DISKIMG`, one MMIO command per 512-byte block.
- 64MB ext2 `lobsteros.img` ships in the repo with a Debian-style layout
  (/etc with motd+passwd+pkg/repos.conf, /home/user, /bin, /var, /usr, ...).
- Kernel mounts it at **/sd** at boot; lazy directory expansion; new files
  created in the guest are allocated in ext2 (inode + dir entry + data) and
  written through on close; existing files update in place.
- Fixed on the way: two ext2 inode-bitmap panics (512B cache vs 1024B
  block_size slice), and a directory-i_size bug that made appended entries
  invisible across reboots.

**Verified live, two fresh boots:**
`echo "PERSIST-FINAL" > /sd/home/user/saved.txt` (boot 1) →
`cat /sd/home/user/saved.txt` → `PERSIST-FINAL` (boot 2). Host image
inspection (strings/debugfs) confirms the bytes on disk.

## r9 (2026-10-18 late): Desktop wave 1 — start menu + notifications

- **Start menu** (A2.1): Windows-style app launcher — click the panel's
  launcher button (or `menu` in the shell) to open a dark rounded panel with
  the 🦞 LobsterOS brand header and scrollable app tiles (Terminal, Files,
  Editor, Calculator, Sys Monitor, Processes, Settings, Network, App Store,
  Images, About). Hover/keyboard navigation, Esc/outside-click close,
  Enter launches; tiles focus existing windows instead of duplicates.
  Verified visually in the live GUI.
- **Notifications** (A2.3 core): toast cards slide in top-right with
  severity accent bars (info/success/warning/error), auto-expire, stack,
  and keep a 24-entry history. Persistent-disk mount now posts a toast;
  `notify <title> <body>` shell command for testing. Verified visually.
- **Shell automation hooks**: `menu`, `app <name>`, `notify` — driving the
  desktop from the serial console for CI-able GUI tests.
- New graphics primitive: `fill_rounded_rect`.
- `docs/DESKTOP_PLAN.md`: the master feature tree (A–E) tracking the road to
  a full Windows/macOS-class desktop.

## r10 (2026-10-18 late-night): desktop wave 2 — the color fix + wallpaper/theme

- **THE color-order fix**: every desktop screenshot until now rendered
  red/blue-swapped (the emulated display reads byte0 as RED; the kernel
  packed 0xFFRRGGBB). A system-wide swizzle at every framebuffer write
  fixed the entire UI at once. Proven by screendump pixel test:
  red→red, blue→blue. The desktop finally shows the true Nord Aurora
  blue gradient.
- **Wallpaper engine**: 7 gradient presets, live repaint on change,
  persisted to /sd/etc/wallpaper.conf and restored at boot.
- **Theme**: Light/Dark toggle persisted to /sd/etc/theme.conf, restored
  at boot, no longer clobbered by init.
- **Desktop right-click menu**: New File/Folder, Open Terminal, Display
  Settings, Change Wallpaper, Toggle Dark/Light, Refresh — all live.
- **vfs::write_file** now writes through to the ext2 backing store, so
  kernel-side persistence (settings files) actually lands on disk.

## r11 (2026-10-19 early): desktop waves 3-4 — tray popups, window chrome, BROWSER

- **System tray popups**: click the clock/network/volume/battery tray icons
  (or `tray <kind>` from the shell) — Clock popup doubles as the
  notification center (big time, uptime, 6 most recent notifications),
  Network popup shows link state + IP + gateway, Volume/Battery show level
  bars.
- **Uniform themed window chrome**: one draw_frame() for all windows —
  themed title bar with focused/unfocused states, rounded close button,
  shadow, rounded body — plus a 160ms open/close/minimize scale animation
  engine. System Monitor converted; other apps convert incrementally.
- **Lobster Browser (first web browser!)**: URL bar, Back/Fwd/Go, HTML-strip
  renderer (block-tag line breaking, entity decoding), scrollable page,
  history back/forward — fetching live over the host HTTP proxy. Visual
  verified: http://example.com renders "Example Domain" in the window.
- Start menu now includes Browser (12 tiles total).

## r12 (2026-10-19 early): unified window chrome across all apps

All core windows (Terminal, File Manager, Calculator, Process Manager,
System Monitor, Browser) now render through one themed frame — consistent
Light/Dark-aware title bars, rounded X close buttons, drop shadows, focus
states, and a 160ms open animation on creation. Screenshot-verified: the
desktop reads as one cohesive polished environment.

## r13 (2026-10-19): Alt-Tab switcher, file manager polish, real editor

- **Alt-Tab switcher overlay**: hold Alt, press Tab — centered card with
  window tiles (glyph + title + selection highlight), Shift+Tab reverses,
  releasing Alt focuses the selection. `alttab` shell command for testing.
- **File Manager rebuilt**: Places sidebar (Home/Storage/System/Temp/Root
  with active highlight), Up/Refresh toolbar + path pill, zebra-striped
  list with type markers and sizes, themed status bar.
- **Real text editor** (was a QEMU stub): opens/saves persisted files via
  /sd, line numbers, cursor, modified indicator, status line. `edit
  <path>` opens any file on disk.

## r15 (2026-10-19): panel CPU graph, window snapping, Settings app

- **Panel CPU mini-graph**: 60-second sparkline in the taskbar with
  green/yellow/red load bars (visual-verified).
- **Window management**: double-click title bar toggles maximize;
  drag-to-edge snaps left/right half or maximizes on top edge.
- **Settings app**: real window with Appearance (theme + wallpaper presets
  with swatches), Display, Network, Storage, and About pages — all showing
  live system data. `settings` / `settings next` shell commands.

## r16 (2026-10-19): shell-in-a-window + icon selection

- **Interactive shell in the Terminal window** (B1.2): with the Terminal
  focused, GUI keyboard input types into the window — echo, backspace,
  Enter executes through the shell, output + fresh 'lobster:/#' prompt
  render inside the window. Visual-verified: typed 'uptime' shows command,
  uptime output, and prompt in the window. 'gkey' shell command drives
  the input path for CI.
- **Desktop icon selection** (A1.3 core): click-to-select highlight
  (click again to open), hover tint, empty-desktop click clears.
  'iconsel' automation command. Visual-verified: selected icon shows the
  rounded selection box, others plain.

## r17 (2026-10-19): file operations persist — delete + rename

- **Deletes persist**: removing a file (shell rm, FM Delete key, fm del)
  now unlinks it in the ext2 backing store — entries stay gone across
  reboots. Fixed a third 512/1024 bitmap slice panic in free_inode.
- **Renames persist, correctly**: ext2 rename is now an in-place name edit
  in the directory block (inode/type/rec_len preserved). The earlier
  unlink+relink implementation corrupted file types; fresh-disk round
  trips verify name, content, AND type survive rename + reboot.
- **File Manager keyboard**: with the Files window focused — arrows
  select, Enter opens, Backspace goes up, Delete removes (with toast),
  F5 refreshes, 'r' quick-renames. 'fm <nav|up|sel|del|rename|ls>' shell
  commands drive everything for CI.
- Fixed a borrow-aliasing bug where the old filename silently became the
  new one before the ext2 sync.

## r18 (2026-10-19): browser bookmarks + history persist (B5.2)

Bookmarks star-toggle to /sd/etc/browser/bookmarks (dir auto-created,
loaded at boot, count shown in the chrome bar) and the last-20-URL history
persists to /sd/etc/browser/history. Verified round trip: bookmarked URL
present on the host disk image and loaded in a fresh boot.

## r19 (2026-10-19): copy/move persist — storage ops complete

- File AND recursive directory copies persist to disk (fm copy/move +
  write_file path). Verified: files and a full notes/ directory tree
  survive fresh boots.
- ext2 allocator hardened: inodes still holding links are never handed
  out, fixing corruption from a past free/relink race.
- With r17/r18: create, mkdir, rename, delete, copy, move, and
  truncating writes ALL persist across reboots.

## r20 (2026-10-19): accent colors + Image Viewer + session restore

- **Accent color system**: 6 presets (Nord Blue/Coral/Amber/Mint/Violet/
  Cyan), persisted to /sd/etc/theme.conf with the theme, restored at boot,
  live-applied to the panel CPU graph, tray bars, and selection highlights.
- **Image Viewer**: renders real BMP images from disk (24/32-bit, top-down
  and bottom-up rows) in a themed window with fit-scaling. Pixel-verified:
  a generated gradient renders with exact channel sweeps.
- **Session restore**: /sd/etc/boot_open reopens apps/windows at boot —
  the desktop restores your everyday setup automatically.
- New shell commands: accent, image, wins.

## r21 (2026-10-19): 50× desktop render speedup + timer fix

- **CNTVCT uptime**: uptime was frozen in graphics boots (QEMU raspi5b
  has no BCM2711 MMIO system timer). Now uses the ARMv8 architectural
  counter — animations complete, clocks/monitors tick.
- **Render pipeline**: draw_rect/fill_rect/draw_text fast paths (hoisted
  swizzle, scanline writes) + dirty-tracking render gating (HID events
  and window ops drive redraws; static apps don't repaint every frame).
  wm::tick steady state: 374ms → ~6ms.
- **30fps frame limiter** for coherent frames + lower host CPU burn.
- **Image Viewer double-fx fix**: colors were r/b-swapped; gradient now
  pixel-exact. Session-restore opens the viewer clear of the terminal.

## r22 (2026-10-19): panel clock + tray fixed

- The panel clock, workspace label adjacency and the network/volume/
  battery tray never rendered: init positioned them from fb.width before
  the framebuffer existed (u32 underflow → clipped forever). Now
  re-anchored from the live fb every frame. Clock + tray verified by
  pixel analysis (4/4 dumps).
