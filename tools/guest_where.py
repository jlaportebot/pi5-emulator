#!/usr/bin/env python3
"""Find where the guest is stuck.

Boots the guest with the serial console on a UNIX socket (so commands can be
typed) and the QEMU monitor on another socket. After issuing a command, reads
the CPU registers from the monitor and resolves the PC to a source line with
addr2line.

Usage:
  python3 tools/guest_where.py --kernel IMG --addr2line ELF [--disk PATH] CMD
"""
import argparse
import re
import socket
import subprocess
import sys
import time

import boot_common
from lobstervm.config import VmError

ADDR2LINE_TIMEOUT_S = 60


def connect(path, attempts=40):
    for _ in range(attempts):
        try:
            sock = socket.socket(socket.AF_UNIX)
            sock.connect(path)
            return sock
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"could not connect to {path}")


def monitor_cmd(sock, line, settle=1.0):
    if line:
        sock.sendall((line + "\n").encode())
    time.sleep(settle)
    data = b""
    sock.settimeout(1.5)
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        pass
    sock.settimeout(None)
    return data.decode("utf-8", "replace")


def serial_send(sock, text, settle=1.0):
    sock.sendall(text.encode())
    time.sleep(settle)


def report_pc(monitor, addr2line_elf):
    """Print the guest PC, nearby instructions and the source line."""
    regs = monitor_cmd(monitor, "info registers", settle=1.5)
    match = re.search(r"^\s*pc\s*=\s*0x([0-9a-fA-F]+)", regs, re.M)
    if not match:
        print("could not read pc from monitor:\n", regs[:2000])
        return 1
    pc = int(match.group(1), 16)
    print(f"guest PC = 0x{pc:x}")

    info = monitor_cmd(monitor, f"x/8i 0x{pc:x}", settle=1.5)
    print(info[-1500:])

    result = subprocess.run(
        ["addr2line", "-e", addr2line_elf, "-f", "-C", f"0x{pc:x}"],
        capture_output=True, text=True, timeout=ADDR2LINE_TIMEOUT_S)
    print("addr2line:", result.stdout.strip(), result.stderr.strip())
    return 0


def run_where(args, directory, overlay):
    monitor_path = directory / "monitor.sock"
    serial_path = directory / "serial.sock"
    command = boot_common.qemu_command(
        args.kernel,
        ["-chardev", f"socket,id=ser0,path={serial_path},server=on,wait=off",
         "-serial", "chardev:ser0"],
        ["-monitor", f"unix:{monitor_path},server,nowait"], overlay)
    proc = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        monitor = connect(str(monitor_path))
        serial = connect(str(serial_path))
        monitor_cmd(monitor, "", settle=0.5)
        time.sleep(args.boot)
        serial_send(serial, args.command + "\n", settle=args.settle)
        return report_pc(monitor, args.addr2line)
    finally:
        proc.kill()
        proc.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel", required=True)
    parser.add_argument("--addr2line", required=True)
    parser.add_argument("--disk", default="",
                        help="qcow2 disk to attach (a throwaway overlay is used)")
    parser.add_argument("--boot", type=float, default=14.0)
    parser.add_argument("--settle", type=float, default=10.0)
    parser.add_argument("command")
    args = parser.parse_args()

    try:
        boot_common.resolve_components(args.kernel)
        with boot_common.work_dir("lb-where-") as directory:
            overlay = boot_common.make_overlay(args.disk, directory) if args.disk else None
            return run_where(args, directory, overlay)
    except (VmError, RuntimeError) as error:
        print(f"[FAIL] {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
