#!/usr/bin/env python3
"""Unit tests for tools/mkbase.py: manifest handling and image building.

Building tests skip cleanly when e2fsprogs is not installed.
"""
import io
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
sys.path.insert(0, str(REPO_ROOT / "tools"))

import mkbase
from lobstervm.config import VmError

MIB = 1 << 20
TINY_SIZE = 4 * MIB
E2FS_TOOLS = ("mke2fs", "tune2fs", "debugfs", "dumpe2fs", "e2fsck")
needs_e2fsprogs = unittest.skipUnless(
    all(shutil.which(tool) for tool in E2FS_TOOLS), "e2fsprogs is required")

SAMPLE_ENTRIES = [
    mkbase.Entry("d", 0o755, 0, 0, "/"),
    mkbase.Entry("d", 0o700, 1000, 1000, "/empty dir"),
    mkbase.Entry("f", 0o640, 1000, 100, "/etc/motd"),
    mkbase.Entry("d", 0o755, 0, 0, "/etc"),
    mkbase.Entry("l", 0o777, 0, 0, "/etc/link"),
]


class TestManifest(unittest.TestCase):
    def test_roundtrip_is_sorted_and_lossless(self):
        text = mkbase.format_manifest(SAMPLE_ENTRIES)
        paths = [line.split(" ", 4)[4] for line in text.splitlines()]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(sorted(mkbase.parse_manifest(text), key=lambda e: e.path),
                         sorted(SAMPLE_ENTRIES, key=lambda e: e.path))

    def test_path_with_space_survives(self):
        text = mkbase.format_manifest([SAMPLE_ENTRIES[1]])
        self.assertEqual(mkbase.parse_manifest(text)[0].path, "/empty dir")

    def test_blank_lines_ignored(self):
        self.assertEqual(mkbase.parse_manifest("\n\nd 0755 0 0 /\n\n")[0].path, "/")

    def test_malformed_lines_rejected(self):
        for bad in ("x 0755 0 0 /a", "f 0755 0 /a", "f 0755 0 0 relative",
                    "f 0xyz 0 0 /a", "f 0755 root 0 /a"):
            with self.subTest(line=bad), self.assertRaises(VmError):
                mkbase.parse_manifest(bad)

    def test_mode_is_octal_with_four_digits(self):
        line = mkbase.format_manifest([mkbase.Entry("f", 0o4755, 0, 0, "/su")])
        self.assertTrue(line.startswith("f 4755 0 0 /su"))

    def test_mke2fs_args_encode_original_geometry(self):
        args = mkbase.mke2fs_args(Path("o.img"), 64 * MIB, Path("stage"))
        self.assertIn("LOBSTEROS", args)
        self.assertIn(mkbase.VOLUME_UUID, args)
        self.assertEqual(args[args.index("-b") + 1], "1024")
        self.assertEqual(args[args.index("-I") + 1], "256")
        self.assertEqual(args[-1], "65536")

    def test_committed_manifest_parses(self):
        entries = mkbase.parse_manifest(mkbase.MANIFEST_FILE.read_text())
        self.assertIn("/", [e.path for e in entries])
        for entry in entries:
            if entry.kind != "d":
                self.assertTrue((mkbase.TREE_DIR / entry.path.lstrip("/")).exists()
                                or (mkbase.TREE_DIR / entry.path.lstrip("/")).is_symlink())


class TestMissingBaseImage(unittest.TestCase):
    def test_error_points_at_mkbase(self):
        from lobstervm import paths
        missing = Path(tempfile.gettempdir()) / "definitely-missing-base.img"
        with patch.dict("os.environ", {"LOBSTER_BASE_DISK": str(missing)}), \
                patch.object(paths, "REPO_ROOT", missing.parent / "no-such-repo"), \
                self.assertRaises(paths.MissingComponent) as caught:
            paths.base_disk()
        self.assertIn("python3 tools/mkbase.py", str(caught.exception))


@needs_e2fsprogs
class TestBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tree = self.tmp / "tree"
        (self.tree / "etc").mkdir(parents=True)
        (self.tree / "etc" / "motd").write_text("hello lobster\n")
        (self.tree / "etc" / "link").symlink_to("motd")
        self.manifest = self.tmp / "m.manifest"
        self.manifest.write_text(mkbase.format_manifest(SAMPLE_ENTRIES))
        self.image = self.tmp / "out.img"

    def build(self, output=None):
        return mkbase.build_image(self.tree, self.manifest, output or self.image,
                                  TINY_SIZE)

    def test_build_applies_ownership_modes_and_empty_dirs(self):
        self.build()
        tree = mkbase.read_image_tree(self.image)
        self.assertEqual(tree["/etc/motd"]["uid"], 1000)
        self.assertEqual(tree["/etc/motd"]["gid"], 100)
        self.assertEqual(tree["/etc/motd"]["mode"], 0o640)
        self.assertEqual(tree["/empty dir"]["mode"], 0o700)
        self.assertEqual(tree["/etc/link"]["target"], "motd")
        self.assertEqual(mkbase.read_file(self.image, "/etc/motd"), b"hello lobster\n")
        self.assertNotIn("/lost+found", tree)

    def test_build_has_original_geometry_and_is_fsck_clean(self):
        self.build()
        facts = mkbase.superblock_facts(self.image)
        self.assertEqual(facts["Filesystem volume name"], "LOBSTEROS")
        self.assertEqual(facts["Filesystem UUID"], mkbase.VOLUME_UUID)
        self.assertEqual(facts["Inode size"], "256")
        self.assertEqual(facts["Block size"], "1024")
        self.assertEqual(facts["Filesystem features"],
                         "ext_attr resize_inode dir_index filetype sparse_super large_file")
        done = subprocess.run(["e2fsck", "-fn", str(self.image)], capture_output=True)
        self.assertEqual(done.returncode, 0)

    def test_two_builds_are_equivalent(self):
        first = self.build()
        second = self.build(self.tmp / "second.img")
        self.assertEqual(mkbase.compare_images(first, second), [])

    def test_compare_reports_content_and_owner_differences(self):
        first = self.build()
        (self.tree / "etc" / "motd").write_text("changed\n")
        second = self.build(self.tmp / "second.img")
        problems = mkbase.compare_images(first, second)
        self.assertEqual(len(problems), 1)
        self.assertIn("/etc/motd", problems[0])

    def test_check_image_flags_difference_and_missing_reference(self):
        reference = self.build()
        self.assertEqual(mkbase.check_image(self.tree, self.manifest, reference,
                                            TINY_SIZE), [])
        (self.tree / "extra.txt").write_text("x")
        with self.assertRaises(VmError):
            mkbase.check_image(self.tree, self.manifest, reference, TINY_SIZE)
        with self.assertRaises(VmError):
            mkbase.check_image(self.tree, self.manifest, self.tmp / "none.img",
                               TINY_SIZE)

    def test_manifest_entry_without_file_is_rejected(self):
        (self.tree / "etc" / "motd").unlink()
        with self.assertRaises(VmError) as caught:
            self.build()
        self.assertIn("/etc/motd", str(caught.exception))
        self.assertFalse(self.image.exists())

    def test_extract_then_build_roundtrips(self):
        built = self.build()
        tree, manifest = self.tmp / "tree2", self.tmp / "m2"
        tree.mkdir()
        mkbase.extract_image(built, tree, manifest)
        rebuilt = mkbase.build_image(tree, manifest, self.tmp / "again.img", TINY_SIZE)
        self.assertEqual(mkbase.compare_images(built, rebuilt), [])

    def test_cli_refuses_overwrite_without_force(self):
        self.image.write_bytes(b"precious")
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            code = mkbase.main(["--output", str(self.image)])
        self.assertEqual(code, 1)
        self.assertEqual(self.image.read_bytes(), b"precious")
        self.assertIn("--force", err.getvalue())

    def test_cli_rejects_oversized_volume(self):
        err = io.StringIO()
        with redirect_stderr(err):
            code = mkbase.main(["--output", str(self.tmp / "big.img"), "--size", "2G"])
        self.assertEqual(code, 1)
        self.assertIn("128 block groups", err.getvalue())


if __name__ == "__main__":
    unittest.main()


class TestBootOpenFile(unittest.TestCase):
    def test_boot_open_is_empty_so_no_session_entries_are_restored(self):
        from tools import mkbase
        boot_open = mkbase.TREE_DIR / "etc" / "boot_open"
        self.assertEqual(boot_open.stat().st_size, 0)
