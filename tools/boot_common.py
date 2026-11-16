"""Shared plumbing for the standalone boot/debug helper tools.

Resolves QEMU, kernel and DTB through ``lobstervm.paths`` and keeps every
run's sockets, logs and disk overlays in a private temporary directory.
"""
import json
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lobstervm import paths
from lobstervm.config import VmError, run_qemu_img

MACHINE = "raspi5b,graphics=on"
MEMORY = "4G"
CPU = "cortex-a76"


def default_kernel():
    """Kernel path for ``--kernel`` defaults; resolved lazily for ``--help``."""
    try:
        return str(paths.default_kernel())
    except paths.MissingComponent:
        return "kernel8.img"


@contextmanager
def work_dir(prefix):
    """A private per-run directory, removed on exit even on errors."""
    directory = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def make_overlay(disk, directory):
    """Throwaway qcow2 overlay on ``disk``; the original is never written."""
    info = json.loads(run_qemu_img(["info", "--output=json", str(disk)]))
    overlay = Path(directory) / "overlay.qcow2"
    run_qemu_img(["create", "-f", "qcow2", "-b", str(Path(disk).resolve()),
                  "-F", info["format"], str(overlay)])
    return overlay


def qemu_command(kernel, serial_args, monitor_args, overlay=None):
    """Headless raspi5b command line using the repo-resolved binaries."""
    command = [
        str(paths.qemu_binary()), "-M", MACHINE, "-m", MEMORY, "-cpu", CPU,
        "-kernel", str(kernel), "-dtb", str(paths.default_dtb()),
        "-display", "none", *serial_args, *monitor_args,
    ]
    if overlay is not None:
        command += ["-drive", f"if=none,id=lobsterdisk,file={overlay},format=qcow2"]
    return command


def resolve_components(kernel):
    """Fail early with one clear line if QEMU/kernel/DTB are missing."""
    try:
        paths.qemu_binary()
        paths.default_dtb()
    except paths.MissingComponent as error:
        raise VmError(str(error)) from error
    if not Path(kernel).is_file():
        raise VmError(f"kernel not found: {kernel}")
