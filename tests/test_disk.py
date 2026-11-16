#!/usr/bin/env python3
"""Unit tests for ``lobstervm disk`` (E2.7): info, export, import, resize.

Tests that need qemu-img / e2fsprogs skip cleanly when the tools are absent.
Run with: python3 -m unittest discover -s tests -v
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lobstervm import disk
from lobstervm.cli import build_parser, main
from lobstervm.config import VmConfig, VmError, VmStore
from lobstervm.machine import Machine

MIB = 1 << 20
BASE_BLOCKS_1K = 4096
EXT2_TOOLS = ("qemu-img", "mke2fs", "e2fsck", "resize2fs")
needs_ext2_tools = unittest.skipUnless(
    all(shutil.which(tool) for tool in EXT2_TOOLS),
    "qemu-img and e2fsprogs are required")
needs_mke2fs = unittest.skipUnless(shutil.which("mke2fs"), "mke2fs is required")


def make_ext2_image(path, blocks_1k=BASE_BLOCKS_1K):
    """Create a small real ext2 image (1 KiB blocks) at ``path``."""
    subprocess.run(["mke2fs", "-q", "-F", "-t", "ext2", "-b", "1024", str(path),
                    str(blocks_1k)], check=True, capture_output=True)
    return Path(path)


def fsck_is_clean(raw_path):
    result = subprocess.run(["e2fsck", "-fn", str(raw_path)], capture_output=True)
    return result.returncode == 0


def qemu_info(path):
    return disk.image_info(Path(path))


class DiskTestCase(unittest.TestCase):
    """Builds an isolated store whose base image is a tiny real ext2 volume."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = VmStore(self.tmp / "store")
        self.base = make_ext2_image(self.tmp / "base.img") \
            if shutil.which("mke2fs") else self.tmp / "base.img"
        env = patch.dict(os.environ, {"LOBSTER_BASE_DISK": str(self.base)})
        env.start()
        self.addCleanup(env.stop)

    def make_vm(self, name="vm"):
        self.store.create(VmConfig(name=name))
        return name


class TestParseSize(unittest.TestCase):
    def test_plain_bytes(self):
        self.assertEqual(disk.parse_size("1048576"), MIB)

    def test_binary_suffixes(self):
        self.assertEqual(disk.parse_size("128M"), 128 * MIB)
        self.assertEqual(disk.parse_size("1G"), 1 << 30)
        self.assertEqual(disk.parse_size("512k"), 512 * 1024)
        self.assertEqual(disk.parse_size("1GiB"), 1 << 30)
        self.assertEqual(disk.parse_size(" 2MB "), 2 * MIB)

    def test_invalid_text_rejected(self):
        for text in ("", "abc", "12X", "-5M", "1.5G", "M", "0x10", "1 2"):
            with self.subTest(text=text), self.assertRaises(VmError):
                disk.parse_size(text)

    def test_zero_rejected(self):
        with self.assertRaises(VmError):
            disk.parse_size("0")

    def test_validate_new_size_boundaries(self):
        current = 64 * MIB
        disk.validate_new_size(65 * MIB, current)
        disk.validate_new_size(disk.MAX_VOLUME_BYTES, current)
        for bad in (current, current - MIB, 65 * MIB + 512,
                    disk.MAX_VOLUME_BYTES + MIB):
            with self.subTest(size=bad), self.assertRaises(VmError):
                disk.validate_new_size(bad, current)

    def test_limit_error_names_the_guest_driver(self):
        with self.assertRaises(VmError) as caught:
            disk.validate_new_size(disk.MAX_VOLUME_BYTES + MIB, 64 * MIB)
        self.assertIn("128 block groups", str(caught.exception))

    def test_human_size(self):
        self.assertEqual(disk.human_size(64 * MIB), "64.0 MiB")
        self.assertEqual(disk.human_size(512), "512 B")


@needs_mke2fs
class TestExt2Validation(DiskTestCase):
    def test_real_ext2_accepted(self):
        disk.require_ext2(self.base)

    def test_non_ext2_rejected(self):
        junk = self.tmp / "junk.img"
        junk.write_bytes(b"\x00" * (4 * MIB))
        with self.assertRaises(VmError) as caught:
            disk.require_ext2(junk)
        self.assertIn("ext2", str(caught.exception))

    def test_short_file_rejected(self):
        tiny = self.tmp / "tiny.img"
        tiny.write_bytes(b"abc")
        with self.assertRaises(VmError):
            disk.require_ext2(tiny)

    def test_group_count_over_guest_limit_rejected(self):
        big = self.tmp / "big.img"
        make_ext2_image(big, blocks_1k=8192)
        with patch.object(disk, "MAX_BLOCK_GROUPS", 0), \
                self.assertRaises(VmError) as caught:
            disk.require_ext2(big)
        self.assertIn("block groups", str(caught.exception))


@needs_ext2_tools
class TestInfoAndExport(DiskTestCase):
    def test_info_reports_backing_and_size(self):
        name = self.make_vm()
        out = "\n".join(disk.describe(self.store, name))
        self.assertIn("qcow2", out)
        self.assertIn(str(self.base), out)
        self.assertIn(str(self.base.stat().st_size), out)

    def test_info_on_standalone_disk(self):
        name = self.make_vm()
        target = self.tmp / "flat.qcow2"
        disk.export_disk(self.store, name, target, "qcow2")
        shutil.copy(target, self.store.disk_path(name))
        out = "\n".join(disk.describe(self.store, name))
        self.assertIn("none (standalone)", out)

    def test_export_raw_has_no_backing_and_same_size(self):
        name = self.make_vm()
        target = self.tmp / "out.img"
        disk.export_disk(self.store, name, target, "raw")
        info = qemu_info(target)
        self.assertEqual(info["format"], "raw")
        self.assertNotIn("backing-filename", info)
        self.assertEqual(info["virtual-size"], self.base.stat().st_size)
        self.assertTrue(fsck_is_clean(target))

    def test_export_qcow2_is_standalone(self):
        name = self.make_vm()
        target = self.tmp / "out.qcow2"
        disk.export_disk(self.store, name, target, "qcow2")
        info = qemu_info(target)
        self.assertEqual(info["format"], "qcow2")
        self.assertNotIn("backing-filename", info)

    def test_export_refuses_existing_output_without_force(self):
        name = self.make_vm()
        target = self.tmp / "out.img"
        target.write_bytes(b"keep me")
        with self.assertRaises(VmError):
            disk.export_disk(self.store, name, target, "raw")
        self.assertEqual(target.read_bytes(), b"keep me")
        disk.export_disk(self.store, name, target, "raw", force=True)
        self.assertEqual(qemu_info(target)["format"], "raw")

    def test_export_rejects_unknown_format(self):
        name = self.make_vm()
        with self.assertRaises(VmError):
            disk.export_disk(self.store, name, self.tmp / "o", "vmdk")


@needs_ext2_tools
class TestImport(DiskTestCase):
    def test_non_ext2_import_leaves_old_disk_untouched(self):
        name = self.make_vm()
        before = self.store.disk_path(name).read_bytes()
        junk = self.tmp / "junk.img"
        junk.write_bytes(b"\x00" * (4 * MIB))
        with self.assertRaises(VmError):
            disk.import_disk(self.store, name, junk, yes=True)
        self.assertEqual(self.store.disk_path(name).read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.store.vm_dir(name).iterdir()),
                         ["disk.qcow2", "vm.json"])

    def test_valid_raw_import_is_standalone_qcow2(self):
        name = self.make_vm()
        image = make_ext2_image(self.tmp / "other.img", blocks_1k=8192)
        disk.import_disk(self.store, name, image, yes=True)
        info = qemu_info(self.store.disk_path(name))
        self.assertEqual(info["format"], "qcow2")
        self.assertNotIn("backing-filename", info)
        self.assertEqual(info["virtual-size"], 8192 * 1024)

    def test_valid_qcow2_import_detected_without_extension(self):
        name = self.make_vm()
        exported = self.tmp / "disguised.raw"
        disk.export_disk(self.store, name, exported, "qcow2")
        disk.import_disk(self.store, name, exported, yes=True)
        self.assertEqual(qemu_info(self.store.disk_path(name))["format"], "qcow2")

    def test_import_requires_yes(self):
        name = self.make_vm()
        with self.assertRaises(VmError) as caught:
            disk.import_disk(self.store, name, self.base, yes=False)
        self.assertIn("--yes", str(caught.exception))

    def test_import_missing_image(self):
        name = self.make_vm()
        with self.assertRaises(VmError):
            disk.import_disk(self.store, name, self.tmp / "nope.img", yes=True)


@needs_ext2_tools
class TestResize(DiskTestCase):
    def test_resize_grows_and_stays_clean(self):
        name = self.make_vm()
        new_size = 16 * MIB
        disk.resize_disk(self.store, name, "16M", yes=True)
        info = qemu_info(self.store.disk_path(name))
        self.assertEqual(info["virtual-size"], new_size)
        self.assertNotIn("backing-filename", info)
        check = self.tmp / "check.img"
        disk.export_disk(self.store, name, check, "raw")
        self.assertTrue(fsck_is_clean(check))
        sb_blocks = int.from_bytes(check.read_bytes()[1024 + 4:1024 + 8], "little")
        self.assertEqual(sb_blocks * 1024, new_size)

    def test_resize_requires_yes(self):
        name = self.make_vm()
        with self.assertRaises(VmError) as caught:
            disk.resize_disk(self.store, name, "16M", yes=False)
        self.assertIn("--yes", str(caught.exception))

    def test_resize_rejects_shrink_and_misaligned(self):
        name = self.make_vm()
        for size in ("2M", "4M", "5000000"):
            with self.subTest(size=size), self.assertRaises(VmError):
                disk.resize_disk(self.store, name, size, yes=True)

    def test_failed_resize_leaves_no_work_files(self):
        name = self.make_vm()
        with self.assertRaises(VmError):
            disk.resize_disk(self.store, name, "2M", yes=True)
        self.assertEqual(sorted(p.name for p in self.store.vm_dir(name).iterdir()),
                         ["disk.qcow2", "vm.json"])


@needs_ext2_tools
class TestRunningVmIsRefused(DiskTestCase):
    def test_every_mutating_command_refuses(self):
        name = self.make_vm()
        before = self.store.disk_path(name).read_bytes()
        calls = {
            "export": lambda: disk.export_disk(
                self.store, name, self.tmp / "o.img", "raw"),
            "import": lambda: disk.import_disk(
                self.store, name, self.base, yes=True),
            "resize": lambda: disk.resize_disk(
                self.store, name, "16M", yes=True),
        }
        with patch.object(Machine, "is_running", return_value=True):
            for label, call in calls.items():
                with self.subTest(command=label), \
                        self.assertRaises(VmError) as caught:
                    call()
                self.assertIn("running", str(caught.exception))
        self.assertEqual(self.store.disk_path(name).read_bytes(), before)
        self.assertFalse((self.tmp / "o.img").exists())

    def test_info_works_while_running(self):
        name = self.make_vm()
        with patch.object(Machine, "is_running", return_value=True):
            self.assertTrue(disk.describe(self.store, name))


class TestDiskCli(unittest.TestCase):
    def parse(self, *argv):
        return build_parser().parse_args(["disk", *argv])

    def test_info_arguments(self):
        args = self.parse("info", "vm1")
        self.assertEqual((args.disk_action, args.name), ("info", "vm1"))

    def test_export_arguments(self):
        args = self.parse("export", "vm1", "out.img")
        self.assertEqual((args.output, args.format, args.force),
                         ("out.img", "raw", False))
        args = self.parse("export", "vm1", "out.qcow2", "--format", "qcow2", "--force")
        self.assertEqual((args.format, args.force), ("qcow2", True))

    def test_export_rejects_bad_format(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parse("export", "vm1", "o", "--format", "vmdk")

    def test_import_and_resize_arguments(self):
        args = self.parse("import", "vm1", "in.img", "--yes")
        self.assertEqual((args.image, args.yes), ("in.img", True))
        args = self.parse("resize", "vm1", "128M")
        self.assertEqual((args.size, args.yes), ("128M", False))

    def test_action_is_required(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parse()

    def test_help_for_every_action(self):
        for action in ("info", "export", "import", "resize"):
            with self.subTest(action=action), redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit) as caught:
                self.parse(action, "--help")
            self.assertEqual(caught.exception.code, 0)

    def test_resize_help_warns_about_snapshots(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer), self.assertRaises(SystemExit):
            self.parse("resize", "--help")
        self.assertIn("snapshots", buffer.getvalue())


@needs_ext2_tools
class TestDiskCliEndToEnd(DiskTestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with patch("lobstervm.cli.VmStore", lambda: self.store), \
                redirect_stdout(out), redirect_stderr(err):
            code = main(["disk", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_missing_yes_is_refused_with_exit_1(self):
        name = self.make_vm()
        code, _, err = self.run_cli("resize", name, "16M")
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)
        code, _, err = self.run_cli("import", name, str(self.base))
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)

    def test_info_and_export_through_cli(self):
        name = self.make_vm()
        code, out, _ = self.run_cli("info", name)
        self.assertEqual(code, 0)
        self.assertIn("virtual size", out)
        target = self.tmp / "cli.img"
        code, _, _ = self.run_cli("export", name, str(target))
        self.assertEqual((code, target.is_file()), (0, True))


if __name__ == "__main__":
    unittest.main()
