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
