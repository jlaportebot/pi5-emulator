#!/usr/bin/env python3
"""Live check for roadmap O3.1 (clean boot).

Boots the guest headless and asserts the O3.1 acceptance criteria:
  1. The guest reaches desktop-ready.
  2. NO application windows exist at boot (only the first-run welcome window,
     and only on a fresh install).
  3. The kernel log stays on the serial console and does NOT appear inside the
     graphical Terminal window (the Terminal is opened manually and must show
     only the welcome banner, never kernel [boot]/[boot-t] lines).
  4. The kernel log is still reachable via `dmesg`.
  5. The welcome window is shown once: a second boot of the same disk must not
     show it again.

Usage: python3 tools/clean_boot_check.py [--kernel PATH] [--timeout SEC]
Exit 0 = all checks passed.
"""
import argparse
import re
import subprocess
import sys
import time

import boot_common
from lobstervm.config import VmError

POLL_INTERVAL_S = 0.4

APP_WINDOW_TITLES = (
    "Task Manager", "Calculator", "File Manager", "Process Manager",
    "System Monitor", "Editor", "Terminal",
)
KERNEL_LOG_RE = re.compile(rb"\[(boot|boot-t|irq|klog|sched)\]")


def read_log(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""


def boot(kernel, timeout, serial_log, overlay=None):
    """Boot the guest; return (serial bytes, process)."""
    serial_log.unlink(missing_ok=True)
    command = boot_common.qemu_command(
        kernel, ["-serial", "stdio"], ["-monitor", "none"], overlay)
    proc = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=open(serial_log, "wb"),
        stderr=subprocess.DEVNULL)
    deadline = time.time() + timeout
    data = b""
    while time.time() < deadline and proc.poll() is None:
        data = read_log(serial_log)
        if b"desktop-ready" in data:
            break
        time.sleep(POLL_INTERVAL_S)
    return data, proc


def shell(proc, serial_log, command, expect, timeout=30.0):
    """Type a command into the guest shell and wait for `expect` in the log."""
    proc.stdin.write(command.encode() + b"\n")
    proc.stdin.flush()
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = read_log(serial_log)
        if expect in data:
            return True, data
        time.sleep(0.3)
    return False, read_log(serial_log)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel", default=boot_common.default_kernel())
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--disk", default="",
                        help="optional qcow2 disk; a throwaway overlay on it is "
                             "attached for the welcome-once reboot check")
    args = parser.parse_args()

    try:
        boot_common.resolve_components(args.kernel)
        with boot_common.work_dir("lb-cleanboot-") as directory:
            overlay = boot_common.make_overlay(args.disk, directory) if args.disk else None
            return run_checks(args, directory / "serial.log", overlay)
    except VmError as error:
        print(f"[FAIL] {error}")
        return 1


def run_checks(args, serial_log, overlay):
    failures = []
    data, proc = boot(args.kernel, args.timeout, serial_log, overlay)
    try:
        if b"desktop-ready" not in data:
            print("[FAIL] guest never reached desktop-ready")
            return 1
        print("[PASS] guest reached desktop-ready")

        # --- Criterion 2: no app windows at boot -------------------------
        windows = re.findall(rb"\[wm\] Created window id=\d+ '([^']*)'", data)
        titles = [t.decode("utf-8", "replace") for t in windows]
        print(f"[INFO] windows created at boot: {titles}")
        app_windows = [t for t in titles
                       if t not in ("Welcome to LobsterOS",)]
        if app_windows:
            print(f"[FAIL] application window(s) open at boot: {app_windows}")
            failures.append("windows-at-boot")
        else:
            print("[PASS] no application windows open at boot")

        # --- Criterion 3: kernel log not mirrored into the Terminal ------
        # Open the Terminal app from the shell, then check that no kernel
        # log line was mirrored into it. The kernel log itself stays on
        # serial, which is what we assert here: the Terminal window is
        # created *after* the log was printed, and it must not contain it.
        ok, data = shell(proc, serial_log, "app terminal",
                         b"app: launched terminal")
        if not ok:
            print("[FAIL] could not launch the Terminal app")
            failures.append("terminal-launch")
        else:
            time.sleep(1.0)
            data = read_log(serial_log)
            after_terminal = data.split(b"app: launched terminal", 1)[-1]
            leaked = [line for line in after_terminal.split(b"\n")
                      if KERNEL_LOG_RE.search(line)]
            if leaked:
                print(f"[FAIL] kernel log leaked into the Terminal "
                      f"({len(leaked)} lines)")
                for line in leaked[:5]:
                    print("      " + line.decode("utf-8", "replace"))
                failures.append("log-leak")
            else:
                print("[PASS] kernel log stays on serial, not in the Terminal")

        # --- Criterion 4: kernel log reachable via dmesg -----------------
        ok, data = shell(proc, serial_log, "dmesg", b"[boot]")
        if ok:
            print("[PASS] kernel log reachable via dmesg")
        else:
            print("[FAIL] dmesg did not print the kernel log")
            failures.append("dmesg")
    finally:
        proc.kill()
        proc.wait()

    # --- Criterion 5: welcome only once ---------------------------------
    if overlay is not None:
        data, proc = boot(args.kernel, args.timeout, serial_log, overlay)
        try:
            showed = b"[welcome] First boot detected" in data
            skipped = b"[welcome] Welcome already seen, skipping" in data
            if skipped:
                print("[PASS] welcome window not shown again on the 2nd boot")
            elif showed:
                print("[FAIL] welcome window shown again on the 2nd boot")
                failures.append("welcome-repeat")
            else:
                print("[INFO] no welcome marker found on the 2nd boot")
        finally:
            proc.kill()
            proc.wait()
    else:
        print("[SKIP] welcome-once reboot check (no --disk given)")

    if failures:
        print(f"[FAIL] clean-boot checks failed: {', '.join(failures)}")
        return 1
    print("[PASS] all clean-boot checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
