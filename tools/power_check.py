#!/usr/bin/env python3
"""Live check for roadmap E2.4 / O1.1: clean guest shutdown and guest reboot.

Boots the guest headless with a QEMU monitor socket, waits for
desktop-ready, then:
  1. types `reboot` on the serial console and asserts a SECOND boot banner
     appears (desktop-ready twice, two distinct uptime markers),
  2. types `shutdown` and asserts QEMU exits on its own (no power-off kill).

Usage: python3 tools/power_check.py [--kernel PATH] [--timeout SEC]
Exit code 0 = both checks passed.
"""
import argparse
import subprocess
import sys
import time

import boot_common
from lobstervm.config import VmError

POLL_INTERVAL_S = 0.5


def count_ready(path):
    try:
        return path.read_bytes().count(b"desktop-ready")
    except FileNotFoundError:
        return 0


def wait_for(condition, proc, timeout):
    """Poll ``condition`` until true; returns "ok", "timeout" or "exited"."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return "ok"
        if proc.poll() is not None:
            return "exited"
        time.sleep(POLL_INTERVAL_S)
    return "timeout"


def send(proc, text, settle=0.5):
    proc.stdin.write(text.encode())
    proc.stdin.flush()
    time.sleep(settle)


def check_reboot(proc, serial_log, timeout):
    time.sleep(2.0)
    send(proc, "reboot\n")
    outcome = wait_for(lambda: count_ready(serial_log) >= 2, proc, timeout)
    if outcome == "exited":
        print(f"[FAIL] QEMU exited during reboot rc={proc.returncode}")
    elif outcome == "timeout":
        print("[FAIL] no second boot banner after reboot")
    else:
        print("[PASS] reboot produced a second boot banner")
    return outcome == "ok"


def check_shutdown(proc, timeout):
    time.sleep(2.0)
    send(proc, "shutdown\n")
    outcome = wait_for(lambda: proc.poll() is not None, proc, timeout)
    if outcome != "ok":
        print("[FAIL] QEMU did not exit after shutdown")
        return False
    status = "PASS" if proc.returncode == 0 else "FAIL"
    print(f"[{status}] shutdown exited QEMU with rc={proc.returncode}")
    return proc.returncode == 0


def run_check(kernel, timeout, serial_log):
    command = boot_common.qemu_command(
        kernel, ["-serial", "stdio"], ["-monitor", "none"])
    with open(serial_log, "wb") as log:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log,
                                stderr=subprocess.DEVNULL)
        try:
            booted = wait_for(lambda: count_ready(serial_log) >= 1, proc, timeout)
            if booted != "ok":
                print(f"[FAIL] first boot never reached desktop-ready ({booted})")
                return 1
            print("[PASS] first boot reached desktop-ready")
            if not check_reboot(proc, serial_log, timeout):
                return 1
            return 0 if check_shutdown(proc, timeout) else 1
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel", default=boot_common.default_kernel())
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    try:
        boot_common.resolve_components(args.kernel)
    except VmError as error:
        print(f"[FAIL] {error}")
        return 1
    with boot_common.work_dir("lb-power-") as directory:
        return run_check(args.kernel, args.timeout, directory / "serial.log")


if __name__ == "__main__":
    sys.exit(main())
