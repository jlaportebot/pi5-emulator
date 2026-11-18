"""QEMU command line and environment built by Machine (no QEMU started)."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lobstervm.config import VmConfig, VmStore
from lobstervm.machine import Machine


class TestDeviceProperties(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = VmStore(self.tmpdir)
        # Building the command line resolves the QEMU binary and the kernel;
        # point both at placeholder files so the tests run on a bare checkout
        # (CI has neither a QEMU build nor a kernel image).
        fakes = {}
        for variable, name in (("LOBSTER_QEMU", "qemu-system-aarch64"),
                               ("LOBSTER_KERNEL", "kernel8.img")):
            path = Path(self.tmpdir) / name
            path.write_bytes(b"")
            fakes[variable] = str(path)
        self.env = patch.dict(os.environ, fakes)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def command(self, **fields):
        self.store.create(VmConfig(name="vm", **fields))
        return Machine("vm", self.store)._qemu_command("none", None)

    def test_resolution_becomes_width_and_height_properties(self):
        command = self.command(resolution="1280x720")
        self.assertIn("lobster-sys.width=1280", command)
        self.assertIn("lobster-sys.height=720", command)

    def test_no_share_property_without_shared_folder(self):
        command = self.command()
        self.assertFalse([arg for arg in command if arg.startswith("lobster-share")])

    def test_shared_folder_becomes_dir_property(self):
        folder = Path(self.tmpdir) / "files"
        folder.mkdir()
        command = self.command(shared_folder=str(folder))
        self.assertIn(f"lobster-share.dir={folder}", command)

    def test_commas_in_shared_folder_are_doubled(self):
        folder = Path(self.tmpdir) / "a,b"
        folder.mkdir()
        command = self.command(shared_folder=str(folder))
        self.assertIn(f"lobster-share.dir={self.tmpdir}/a,,b", command)

    def test_spaces_in_shared_folder_stay_one_argument(self):
        folder = Path(self.tmpdir) / "my files"
        folder.mkdir()
        command = self.command(shared_folder=str(folder))
        self.assertIn(f"lobster-share.dir={folder}", command)

    def test_legacy_env_variables_are_removed(self):
        self.store.create(VmConfig(name="vm"))
        machine = Machine("vm", self.store)
        stray = {"LBST_RES": "1x1", "LBST_SHARE": "/x", "LBST_DISKIMG": "/y"}
        with patch.dict(os.environ, stray):
            env = machine._qemu_env()
        for name in stray:
            self.assertNotIn(name, env)


if __name__ == "__main__":
    unittest.main()
