"""Acceleration choice: availability decision table, config, command line."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from image_stubs import ImageStubs

from lobstervm import accel
from lobstervm.config import VmConfig, VmError, VmStore
from lobstervm.machine import Machine, require_accel


STUBS = ImageStubs()


def setUpModule():
    STUBS.start()


def tearDownModule():
    STUBS.stop()


def reason(platform_name, machine, accessible):
    return accel.kvm_unavailable_reason(platform_name, machine, lambda _device: accessible)


class TestKvmAvailability(unittest.TestCase):
    def test_linux_aarch64_with_device_access_is_available(self):
        self.assertIsNone(reason("linux", "aarch64", True))

    def test_arm64_spelling_counts_as_aarch64(self):
        self.assertIsNone(reason("linux", "arm64", True))

    def test_non_linux_host_is_refused(self):
        self.assertIn("Linux host", reason("darwin", "arm64", True))
        self.assertIn("Linux host", reason("win32", "AMD64", True))

    def test_x86_linux_host_is_refused(self):
        self.assertIn("aarch64", reason("linux", "x86_64", True))

    def test_missing_device_access_is_refused(self):
        self.assertIn("/dev/kvm", reason("linux", "aarch64", False))

    def test_platform_is_checked_before_device(self):
        self.assertIn("Linux host", reason("darwin", "arm64", False))

    def test_device_probe_uses_read_write_access(self):
        with patch("lobstervm.accel.os.access", return_value=True) as probe:
            self.assertTrue(accel._kvm_device_accessible("/dev/kvm"))
        probe.assert_called_once_with("/dev/kvm", os.R_OK | os.W_OK)


class TestRequireAccel(unittest.TestCase):
    def test_tcg_never_needs_a_probe(self):
        with patch("lobstervm.accel.kvm_unavailable_reason") as probe:
            require_accel("tcg")
        probe.assert_not_called()

    def test_kvm_refusal_names_reason_and_way_back(self):
        with patch("lobstervm.accel.kvm_unavailable_reason", return_value="no /dev/kvm"):
            with self.assertRaises(VmError) as caught:
                require_accel("kvm")
        message = str(caught.exception)
        self.assertIn("no /dev/kvm", message)
        self.assertIn("--accel tcg", message)

    def test_kvm_passes_when_available(self):
        with patch("lobstervm.accel.kvm_unavailable_reason", return_value=None):
            require_accel("kvm")


class TestAccelConfigAndCommand(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = VmStore(self.tmpdir)
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

    def test_default_is_tcg_with_explicit_options(self):
        command = self.command()
        self.assertEqual(command[command.index("-accel") + 1], "tcg")
        self.assertEqual(command[command.index("-cpu") + 1], "cortex-a76")

    def test_kvm_uses_host_cpu(self):
        command = self.command(accel="kvm")
        self.assertEqual(command[command.index("-accel") + 1], "kvm")
        self.assertEqual(command[command.index("-cpu") + 1], "host")

    def test_override_wins_over_config(self):
        self.store.create(VmConfig(name="vm", accel="kvm"))
        command = Machine("vm", self.store)._qemu_command("none", None, "tcg")
        self.assertEqual(command[command.index("-accel") + 1], "tcg")

    def test_config_without_accel_field_loads_as_tcg(self):
        self.store.create(VmConfig(name="vm"))
        path = self.store.vm_dir("vm") / "vm.json"
        raw = json.loads(path.read_text())
        del raw["accel"]
        path.write_text(json.dumps(raw))
        self.assertEqual(self.store.load("vm").accel, "tcg")

    def test_unknown_accel_is_rejected(self):
        with self.assertRaises(VmError):
            VmConfig(name="vm", accel="hvf").validate()

    def test_start_with_kvm_on_unsupported_host_fails_before_spawning(self):
        self.store.create(VmConfig(name="vm", accel="kvm"))
        with patch("lobstervm.accel.kvm_unavailable_reason", return_value="no /dev/kvm"), \
                patch("lobstervm.machine.subprocess.Popen") as popen:
            with self.assertRaises(VmError):
                Machine("vm", self.store).start(display="none")
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
