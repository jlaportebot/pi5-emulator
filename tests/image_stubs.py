"""Stand-ins for qemu-img and the base image on hosts that lack them.

Tests that create VMs only need an overlay file to exist. When the real
tools and a base image are available (Linux dev hosts, CI with
qemu-utils) nothing is replaced, so those runs still exercise qemu-img.
"""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from lobstervm import paths

CREATE_ACTION = "create"
TARGET_INDEX = -1
STUB_OVERLAY = b"stub-overlay"


def _fake_run_qemu_img(args):
    if args[0] == CREATE_ACTION:
        Path(args[TARGET_INDEX]).write_bytes(STUB_OVERLAY)
    return ""


def _real_tools_present():
    try:
        paths.qemu_img_binary()
        paths.base_disk()
    except paths.MissingComponent:
        return False
    return True


class ImageStubs:
    """Context manager; use from ``setUpModule``/``tearDownModule``."""

    def __init__(self):
        self.patches = []
        self.holder = None

    def start(self):
        if _real_tools_present():
            return
        self.holder = tempfile.TemporaryDirectory()
        base = Path(self.holder.name) / "base.img"
        base.write_bytes(b"")
        self.patches = [
            patch("lobstervm.config.run_qemu_img", _fake_run_qemu_img),
            patch.dict(os.environ, {"LOBSTER_BASE_DISK": str(base)}),
        ]
        for active in self.patches:
            active.start()

    def stop(self):
        for active in reversed(self.patches):
            active.stop()
        if self.holder:
            self.holder.cleanup()
