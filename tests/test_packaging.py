"""Packaging scripts: input verification, icons, bundle extraction, smoke helpers."""
import hashlib
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

PACKAGING_DIR = Path(__file__).resolve().parent.parent / "packaging"
sys.path.insert(0, str(PACKAGING_DIR))

import assemble_inputs  # noqa: E402
import build_bundle  # noqa: E402
import make_icons  # noqa: E402
import make_windows_installer  # noqa: E402
import smoke_test  # noqa: E402

KERNEL_BYTES = b"kernel-bytes"
KERNEL_SHA256 = hashlib.sha256(KERNEL_BYTES).hexdigest()


class TempDirCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name).resolve()


class TestChecksums(TempDirCase):
    def test_parse_handles_text_and_binary_markers(self):
        text = f"{KERNEL_SHA256}  kernel8.img\n{KERNEL_SHA256.upper()} *other.bin\n\n"
        sums = assemble_inputs.parse_sha256sums(text)
        self.assertEqual(sums, {"kernel8.img": KERNEL_SHA256, "other.bin": KERNEL_SHA256})

    def test_parse_rejects_malformed_line(self):
        with self.assertRaises(ValueError):
            assemble_inputs.parse_sha256sums("not-a-digest  file\n")

    def test_verify_accepts_matching_file(self):
        (self.tmp / "kernel8.img").write_bytes(KERNEL_BYTES)
        assemble_inputs.verify_files(self.tmp, {"kernel8.img": KERNEL_SHA256}, ["kernel8.img"])

    def test_verify_rejects_tampered_file(self):
        (self.tmp / "kernel8.img").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "sha256"):
            assemble_inputs.verify_files(self.tmp, {"kernel8.img": KERNEL_SHA256},
                                         ["kernel8.img"])

    def test_verify_rejects_unlisted_file(self):
        (self.tmp / "kernel8.img").write_bytes(KERNEL_BYTES)
        with self.assertRaisesRegex(ValueError, "not listed"):
            assemble_inputs.verify_files(self.tmp, {}, ["kernel8.img"])

    def test_versions_file_is_ordered_key_value_lines(self):
        text = assemble_inputs.render_versions([("version", "1.0"), ("kernel_commit", "abc")])
        self.assertEqual(text, "version=1.0\nkernel_commit=abc\n")


class TestFetchKernel(TempDirCase):
    def fake_download(self, repo, tag, patterns, directory):
        (directory / "kernel8.img").write_bytes(KERNEL_BYTES)
        (directory / "COMMIT").write_text("deadbeef\n")
        (directory / "SHA256SUMS").write_text(f"{KERNEL_SHA256}  kernel8.img\n")

    def test_returns_commit_and_removes_sums(self):
        with patch.object(assemble_inputs, "download_release", self.fake_download):
            self.assertEqual(assemble_inputs.fetch_kernel(self.tmp), "deadbeef")
        self.assertFalse((self.tmp / "SHA256SUMS").exists())

    def test_corrupt_kernel_is_rejected(self):
        def corrupt(repo, tag, patterns, directory):
            self.fake_download(repo, tag, patterns, directory)
            (directory / "kernel8.img").write_bytes(b"corrupt")
        with patch.object(assemble_inputs, "download_release", corrupt), \
                self.assertRaises(ValueError):
            assemble_inputs.fetch_kernel(self.tmp)


class TestQemuArchives(TempDirCase):
    def make_tar(self, top="lobster-qemu-linux-x86_64"):
        source = self.tmp / "src" / top / "bin"
        source.mkdir(parents=True)
        (source / "qemu-system-aarch64").write_bytes(b"q")
        archive = self.tmp / "q.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(self.tmp / "src" / top, arcname=top)
        return archive

    def test_extract_tar_returns_single_top_dir(self):
        target = self.tmp / "out"
        target.mkdir()
        root = build_bundle.extract_qemu_archive(self.make_tar(), target)
        self.assertEqual(root.name, "lobster-qemu-linux-x86_64")
        self.assertTrue((root / "bin" / "qemu-system-aarch64").is_file())

    def test_extract_zip_returns_single_top_dir(self):
        archive = self.tmp / "q.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("lobster-qemu-windows-x86_64/bin/qemu-img.exe", b"x")
        target = self.tmp / "out"
        target.mkdir()
        root = build_bundle.extract_qemu_archive(archive, target)
        self.assertTrue((root / "bin" / "qemu-img.exe").is_file())

    def test_archive_with_two_top_dirs_is_rejected(self):
        archive = self.tmp / "q.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("a/x", b"1")
            zipped.writestr("b/x", b"1")
        target = self.tmp / "out"
        target.mkdir()
        with self.assertRaises(SystemExit):
            build_bundle.extract_qemu_archive(archive, target)

    def test_check_qemu_dir_names_missing_binaries(self):
        (self.tmp / "bin").mkdir()
        with self.assertRaises(SystemExit) as caught:
            build_bundle.check_qemu_dir(self.tmp)
        self.assertIn("qemu-img", str(caught.exception))


class TestIcons(TempDirCase):
    def test_iconset_has_every_retina_pair(self):
        names = dict(make_icons.iconset_entries())
        self.assertEqual(names["icon_16x16.png"], 16)
        self.assertEqual(names["icon_512x512@2x.png"], 1024)
        self.assertEqual(len(names), 10)

    def test_png_and_ico_are_written(self):
        written = make_icons.make_icons(self.tmp / "icons")
        names = {path.name for path in written}
        self.assertTrue({"lobstervm.png", "lobstervm.ico"} <= names)
        for path in written:
            if path.suffix in (".png", ".ico"):
                self.assertGreater(path.stat().st_size, 0)


class TestWindowsInstaller(TempDirCase):
    def test_installer_name_matches_release_pattern(self):
        self.assertEqual(make_windows_installer.installer_name("1.2.3"),
                         "LobsterVM-Setup-1.2.3-x64.exe")

    def test_missing_compiler_is_a_clear_error(self):
        with patch("shutil.which", return_value=None), \
                patch.dict("os.environ", {}, clear=True), \
                self.assertRaisesRegex(SystemExit, "ISCC"):
            make_windows_installer.find_compiler()

    def test_script_declares_per_user_install(self):
        script = make_windows_installer.SCRIPT.read_text()
        self.assertIn("PrivilegesRequired=lowest", script)
        self.assertIn("Launch LobsterVM", script)


class TestSmokeHelpers(TempDirCase):
    def test_boot_time_is_parsed(self):
        self.assertEqual(smoke_test.parse_boot_ms("Desktop ready (guest boot 1559 ms)"), 1559)
        self.assertIsNone(smoke_test.parse_boot_ms("nothing"))

    def test_environment_drops_overrides_and_roots_home(self):
        with patch.dict("os.environ", {"LOBSTER_QEMU": "x", "XDG_DATA_HOME": "y", "KEEP": "1"}):
            env = smoke_test.clean_environment(self.tmp)
        self.assertNotIn("LOBSTER_QEMU", env)
        self.assertNotIn("XDG_DATA_HOME", env)
        self.assertEqual(env["KEEP"], "1")
        self.assertEqual(env["HOME"], str(self.tmp))

    def test_blank_screenshot_is_rejected_and_real_one_accepted(self):
        from PIL import Image
        blank = self.tmp / "blank.png"
        Image.new("RGB", (400, 400), (30, 30, 30)).save(blank)
        with patch.object(smoke_test, "MIN_PNG_BYTES", 1), self.assertRaisesRegex(
                SystemExit, "blank"):
            smoke_test.check_screenshot(blank)
        busy = self.tmp / "busy.png"
        image = Image.new("RGB", (400, 400), (30, 30, 30))
        image.paste((255, 255, 255), (0, 0, 200, 400))
        image.save(busy)
        with patch.object(smoke_test, "MIN_PNG_BYTES", 1):
            self.assertGreater(smoke_test.check_screenshot(busy), 100)

    def test_non_png_is_rejected(self):
        junk = self.tmp / "junk.png"
        junk.write_bytes(b"x" * 5000)
        with self.assertRaisesRegex(SystemExit, "not a real PNG"):
            smoke_test.check_screenshot(junk)

    def test_cli_prefix_is_applied(self):
        args = smoke_test.parse_args(["--cli", str(self.tmp / "app"), "--cli-prefix", "cli"])
        self.assertEqual(smoke_test.resolve_command(args)[1:], ["cli"])

    def test_find_cli_reports_missing_executable(self):
        with self.assertRaises(SystemExit):
            smoke_test.find_cli(self.tmp)


if __name__ == "__main__":
    unittest.main()
