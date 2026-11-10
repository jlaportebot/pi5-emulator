"""Locate the emulator's moving parts without hard-coded absolute paths.

Every location can be overridden with an environment variable; otherwise it
is discovered relative to this repository.
"""
import os
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
QEMU_BINARY_NAME = "qemu-system-aarch64"


class MissingComponent(RuntimeError):
    """A required file (QEMU binary, kernel, DTB, disk) could not be found."""


def _first_existing(candidates):
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def qemu_binary():
    """The LobsterOS QEMU fork (it must provide the raspi5b machine)."""
    found = _first_existing([
        os.environ.get("LOBSTER_QEMU"),
        REPO_ROOT.parent / "qemu-src" / "build-opt" / QEMU_BINARY_NAME,
        Path.home() / "qemu-src" / "build-opt" / QEMU_BINARY_NAME,
        shutil.which("qemu-system-aarch64-lobster"),
    ])
    if found is None:
        raise MissingComponent(
            "QEMU fork not found; set LOBSTER_QEMU to qemu-system-aarch64 "
            "built from jlaportebot/lobster-qemu")
    return found


def default_kernel():
    found = _first_existing([
        os.environ.get("LOBSTER_KERNEL"),
        REPO_ROOT / "kernel8.img",
    ])
    if found is None:
        raise MissingComponent(
            "kernel8.img not found; set LOBSTER_KERNEL or copy a kernel "
            f"into {REPO_ROOT}")
    return found


def default_dtb():
    found = _first_existing([
        os.environ.get("LOBSTER_DTB"),
        REPO_ROOT / "bcm2712-rpi-5-b.dtb",
    ])
    if found is None:
        raise MissingComponent("bcm2712-rpi-5-b.dtb not found; set LOBSTER_DTB")
    return found


def base_disk():
    """The pristine ext2 system image every VM's disk is layered on."""
    found = _first_existing([
        os.environ.get("LOBSTER_BASE_DISK"),
        REPO_ROOT / "lobsteros.img",
    ])
    if found is None:
        raise MissingComponent("lobsteros.img not found; set LOBSTER_BASE_DISK")
    return found


def data_home():
    """Where VM definitions and disks live."""
    root = os.environ.get("LOBSTERVM_HOME")
    if root:
        return Path(root)
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "lobstervm"


def runtime_home():
    """Where sockets, FIFOs and pid files of running VMs live."""
    root = os.environ.get("LOBSTERVM_RUNTIME")
    if root:
        return Path(root)
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(xdg) if xdg else Path("/tmp") / f"lobstervm-{os.getuid()}"
    return base / "lobstervm"
