#!/usr/bin/env python3
"""Send shell commands to a running LobsterOS guest over the serial console.

Usage: python3 tools/serial_shell.py --kernel IMG [--disk PATH] CMD [CMD ...]
Prints the tail of the serial log after the commands have run.
"""
import argparse
import subprocess
import sys
import time

import boot_common
from lobstervm.config import VmError

TAIL_BYTES = 3000
KILL_GRACE_S = 1.0


def run_commands(args, directory, overlay):
    log_path = directory / "serial.log"
    command = boot_common.qemu_command(
        args.kernel, ["-serial", "stdio"], ["-monitor", "none"], overlay)
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log,
                                stderr=subprocess.DEVNULL)
        try:
            time.sleep(args.boot)
            type_commands(proc, args.commands, args.settle)
            time.sleep(KILL_GRACE_S)
        finally:
            proc.kill()
            proc.wait()
    return log_path.read_bytes()


def type_commands(proc, commands, settle):
    for command in commands:
        try:
            proc.stdin.write(command.encode() + b"\n")
            proc.stdin.flush()
        except BrokenPipeError:
            print(f"[FAIL] QEMU exited before running: {command}")
            return
        time.sleep(settle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel", required=True)
    parser.add_argument("--disk", default="",
                        help="qcow2 disk to attach (a throwaway overlay is used)")
    parser.add_argument("--boot", type=float, default=12.0)
    parser.add_argument("--settle", type=float, default=3.0)
    parser.add_argument("commands", nargs="+")
    args = parser.parse_args()

    try:
        boot_common.resolve_components(args.kernel)
        with boot_common.work_dir("lb-serial-shell-") as directory:
            overlay = boot_common.make_overlay(args.disk, directory) if args.disk else None
            data = run_commands(args, directory, overlay)
    except VmError as error:
        print(f"[FAIL] {error}")
        return 1
    print(data[-TAIL_BYTES:].decode("utf-8", "replace"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
