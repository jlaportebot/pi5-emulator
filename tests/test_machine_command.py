"""QEMU command line and environment built by Machine (no QEMU started)."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from image_stubs import ImageStubs

from lobstervm.config import VmConfig, VmError, VmStore
from lobstervm.machine import Machine


STUBS = ImageStubs()


def setUpModule():
    STUBS.start()


def tearDownModule():
    STUBS.stop()


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
        self.assertIn(f"lobster-share.dir={self.tmpdir}{os.sep}a,,b", command)

    def _libc_dir(self, name="libc"):
        """A directory that passes validate() as a host compiler libc."""
        libc = Path(self.tmpdir) / name
        (libc / "include").mkdir(parents=True)
        (libc / "liblobsterc.a").write_bytes(b"")
        return libc

    def test_no_compile_libc_property_without_host_compile(self):
        command = self.command()
        self.assertFalse([arg for arg in command if arg.startswith("lobster-net")])

    def test_host_compile_libc_becomes_compile_property(self):
        libc = self._libc_dir()
        command = self.command(host_compile_libc=str(libc))
        self.assertIn(f"lobster-net.compile-libc={libc}", command)

    def test_commas_in_host_compile_libc_are_doubled(self):
        libc = self._libc_dir("a,b")
        command = self.command(host_compile_libc=str(libc))
        self.assertIn(
            f"lobster-net.compile-libc={self.tmpdir}{os.sep}a,,b", command)

    def test_host_compile_libc_without_archive_is_rejected(self):
        libc = Path(self.tmpdir) / "libc"
        (libc / "include").mkdir(parents=True)
        config = VmConfig(name="vm", host_compile_libc=str(libc))
        with self.assertRaises(VmError) as caught:
            config.validate()
        self.assertIn("must contain include/ and liblobsterc.a",
                      str(caught.exception))

    def test_host_compile_libc_without_headers_is_rejected(self):
        libc = self._libc_dir()
        (libc / "include").rmdir()
        config = VmConfig(name="vm", host_compile_libc=str(libc))
        with self.assertRaises(VmError):
            config.validate()

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
