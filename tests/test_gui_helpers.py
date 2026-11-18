#!/usr/bin/env python3
"""Unit tests for lobstervm.gui pure helper functions.

These tests run headless (no Tk() instantiation) and test the
logic extracted from the GUI module.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure headless display for any accidental Tk imports
os.environ["DISPLAY"] = ""

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from image_stubs import ImageStubs

from lobstervm.config import VmConfig, VmStore, VmError, MACHINE_BY_MEMORY_GB
from lobstervm.gui import (
    format_vm_row,
    serial_tail,
    validate_new_vm_params,
)


STUBS = ImageStubs()


def setUpModule():
    STUBS.start()


def tearDownModule():
    STUBS.stop()


class TestFormatVmRow(unittest.TestCase):
    """Tests for format_vm_row helper."""

    def test_basic_formatting(self):
        config = VmConfig(name="test-vm", memory_gb=4, resolution="1920x1080", display="gtk")
        row = format_vm_row(config, "running")
        self.assertIn("test-vm", row)
        self.assertIn("running", row)
        self.assertIn("4GB", row)
        self.assertIn("1920x1080", row)
        self.assertIn("display=gtk", row)

    def test_stopped_state(self):
        config = VmConfig(name="dev", memory_gb=8, resolution="1280x720", display="none")
        row = format_vm_row(config, "stopped")
        self.assertIn("dev", row)
        self.assertIn("stopped", row)
        self.assertIn("8GB", row)
        self.assertIn("1280x720", row)
        self.assertIn("display=none", row)

    def test_long_name_truncation(self):
        # The format uses {name:<20} so long names get padded, not truncated
        config = VmConfig(name="a" * 25, memory_gb=4, resolution="1920x1080")
        row = format_vm_row(config, "running")
        self.assertTrue(row.startswith("a" * 25))


class TestSerialTail(unittest.TestCase):
    """Tests for serial_tail helper."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_tail_reads_last_n_lines(self):
        log_file = self.tmp_path / "serial.log"
        lines = [f"line {i}\n" for i in range(1, 21)]
        log_file.write_text("".join(lines))
        result = serial_tail(log_file, n=5)
        self.assertEqual(result, "line 16\nline 17\nline 18\nline 19\nline 20")

    def test_tail_fewer_lines_than_requested(self):
        log_file = self.tmp_path / "serial.log"
        log_file.write_text("line 1\nline 2\n")
        result = serial_tail(log_file, n=10)
        self.assertEqual(result, "line 1\nline 2")

    def test_tail_empty_file(self):
        log_file = self.tmp_path / "serial.log"
        log_file.write_text("")
        result = serial_tail(log_file, n=5)
        self.assertEqual(result, "")

    def test_tail_nonexistent_file(self):
        log_file = self.tmp_path / "missing.log"
        result = serial_tail(log_file, n=5)
        self.assertEqual(result, "(serial log not found)")

    def test_tail_handles_binary_garbage(self):
        log_file = self.tmp_path / "serial.log"
        log_file.write_bytes(b"line 1\nline 2\xff\xfe\nline 3\n")
        result = serial_tail(log_file, n=10)
        # Should not crash, should replace invalid UTF-8
        self.assertIn("line 1", result)
        self.assertIn("line 3", result)


class TestValidateNewVmParams(unittest.TestCase):
    """Tests for validate_new_vm_params helper (delegates to config.py)."""

    def test_valid_params(self):
        err = validate_new_vm_params("test-vm", 4, "1920x1080", 20)
        self.assertIsNone(err)

    def test_valid_memory_options(self):
        for mem in sorted(MACHINE_BY_MEMORY_GB):
            err = validate_new_vm_params("test", mem, "1920x1080", 20)
            self.assertIsNone(err, f"memory {mem}GB should be valid")

    def test_invalid_memory(self):
        err = validate_new_vm_params("test", 3, "1920x1080", 20)
        self.assertIsNotNone(err)
        self.assertIn("memory must be one of", err)

    def test_invalid_resolution_format(self):
        err = validate_new_vm_params("test", 4, "1920", 20)
        self.assertIsNotNone(err)
        self.assertIn("resolution must look like", err)

    def test_invalid_name(self):
        err = validate_new_vm_params("invalid name!", 4, "1920x1080", 20)
        self.assertIsNotNone(err)
        self.assertIn("invalid VM name", err)

    def test_empty_name(self):
        err = validate_new_vm_params("", 4, "1920x1080", 20)
        self.assertIsNotNone(err)
        self.assertIn("invalid VM name", err)

    def test_disk_size_bounds(self):
        err = validate_new_vm_params("test", 4, "1920x1080", 0)
        self.assertIsNotNone(err)
        self.assertIn("disk size must be between 1 and 1024", err)

        err = validate_new_vm_params("test", 4, "1920x1080", 1025)
        self.assertIsNotNone(err)
        self.assertIn("disk size must be between 1 and 1024", err)

    def test_valid_disk_size_bounds(self):
        err = validate_new_vm_params("test", 4, "1920x1080", 1)
        self.assertIsNone(err)

        err = validate_new_vm_params("test", 4, "1920x1080", 1024)
        self.assertIsNone(err)


class TestVmConfigValidation(unittest.TestCase):
    """Tests for VmConfig validation (reused by GUI dialogs)."""

    def test_valid_config(self):
        config = VmConfig(name="test", memory_gb=4, resolution="1920x1080", display="gtk")
        config.validate()  # Should not raise

    def test_invalid_memory_raises(self):
        config = VmConfig(name="test", memory_gb=3, resolution="1920x1080")
        with self.assertRaises(VmError) as ctx:
            config.validate()
        self.assertIn("memory must be one of", str(ctx.exception))

    def test_invalid_resolution_raises(self):
        config = VmConfig(name="test", memory_gb=4, resolution="80x60")  # too few digits
        with self.assertRaises(VmError) as ctx:
            config.validate()
        self.assertIn("resolution must look like", str(ctx.exception))

    def test_invalid_display_raises(self):
        config = VmConfig(name="test", memory_gb=4, resolution="1920x1080", display="x11")
        with self.assertRaises(VmError) as ctx:
            config.validate()
        self.assertIn("display must be one of", str(ctx.exception))

    def test_invalid_name_raises(self):
        config = VmConfig(name="bad name", memory_gb=4, resolution="1920x1080")
        with self.assertRaises(VmError) as ctx:
            config.validate()
        self.assertIn("invalid VM name", str(ctx.exception))

    def test_shared_folder_validation(self):
        config = VmConfig(name="test", memory_gb=4, resolution="1920x1080", shared_folder="/nonexistent")
        with self.assertRaises(VmError) as ctx:
            config.validate()
        self.assertIn("shared folder is not a directory", str(ctx.exception))

    def test_kernel_validation(self):
        config = VmConfig(name="test", memory_gb=4, resolution="1920x1080", kernel="/nonexistent.img")
        with self.assertRaises(VmError) as ctx:
            config.validate()
        self.assertIn("kernel image not found", str(ctx.exception))


class TestVmStore(unittest.TestCase):
    """Tests for VmStore (used by GUI for listing/creating VMs)."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store = VmStore(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_list_names_empty(self):
        self.assertEqual(self.store.list_names(), [])

    def test_create_and_list(self):
        config = VmConfig(name="vm1", memory_gb=4, resolution="1920x1080")
        self.store.create(config)
        self.assertEqual(self.store.list_names(), ["vm1"])

    def test_create_duplicate_raises(self):
        config = VmConfig(name="vm1", memory_gb=4, resolution="1920x1080")
        self.store.create(config)
        with self.assertRaises(VmError) as ctx:
            self.store.create(config)
        self.assertIn("already exists", str(ctx.exception))

    def test_load_config(self):
        config = VmConfig(name="vm1", memory_gb=8, resolution="1280x720", display="sdl")
        self.store.create(config)
        loaded = self.store.load("vm1")
        self.assertEqual(loaded.name, "vm1")
        self.assertEqual(loaded.memory_gb, 8)
        self.assertEqual(loaded.resolution, "1280x720")
        self.assertEqual(loaded.display, "sdl")

    def test_delete_vm(self):
        config = VmConfig(name="vm1", memory_gb=4, resolution="1920x1080")
        self.store.create(config)
        self.store.delete("vm1")
        self.assertEqual(self.store.list_names(), [])

    def test_delete_nonexistent_raises(self):
        with self.assertRaises(VmError) as ctx:
            self.store.delete("nonexistent")
        self.assertIn("no such VM", str(ctx.exception))

    def test_snapshots_empty(self):
        config = VmConfig(name="vm1", memory_gb=4, resolution="1920x1080")
        self.store.create(config)
        # Snapshots require qemu-img; just verify the method exists and returns list
        tags = self.store.snapshots("vm1")
        self.assertIsInstance(tags, list)


if __name__ == "__main__":
    unittest.main(verbosity=2)