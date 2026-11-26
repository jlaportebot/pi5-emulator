#!/usr/bin/env python3
"""
Unit tests for lobstervm (E2.8).

Run with: python3 -m unittest discover -s tests -v
These tests do NOT require QEMU to be present.
"""
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

# Ensure lobstervm is importable
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from image_stubs import ImageStubs

from lobstervm.config import (
    VmConfig, VmStore, VmError,
    MACHINE_BY_MEMORY_GB, DISPLAY_BACKENDS,
)
from lobstervm.machine import (
    Machine, _read_until_prompt, _strip_hmp_echo, _parse_snapshot_table,
    _shell_output
)
from lobstervm import paths
from lobstervm.cli import main, build_parser, _apply_config_options


# ════════════════════════════════════════════════════════════════════════
# Config validation tests
# ════════════════════════════════════════════════════════════════════════

STUBS = ImageStubs()


def setUpModule():
    STUBS.start()


def tearDownModule():
    STUBS.stop()


class TestConfigValidation(unittest.TestCase):
    """Tests for VmConfig validation."""

    def test_valid_config_roundtrip(self):
        """Valid config should round-trip through vm.json."""
        with tempfile.TemporaryDirectory() as tmp:
            store = VmStore(tmp)
            config = VmConfig(
                name="testvm",
                memory_gb=4,
                resolution="1920x1080",
                display="gtk",
                audio=True,
                shared_folder="",
                kernel="",
                description="test vm",
            )
            store.create(config)
            loaded = store.load("testvm")
            self.assertEqual(loaded.name, "testvm")
            self.assertEqual(loaded.memory_gb, 4)
            self.assertEqual(loaded.resolution, "1920x1080")
            self.assertEqual(loaded.display, "gtk")
            self.assertTrue(loaded.audio)

    def test_invalid_memory_size(self):
        """Invalid memory size should raise VmError."""
        config = VmConfig(name="test", memory_gb=3)
        with self.assertRaises(VmError) as cm:
            config.validate()
        self.assertIn("memory must be one of", str(cm.exception))

    def test_invalid_resolution_string(self):
        """Invalid resolution format should raise VmError."""
        config = VmConfig(name="test", resolution="1920")
        with self.assertRaises(VmError) as cm:
            config.validate()
        self.assertIn("resolution must look like", str(cm.exception))

    def test_invalid_display_backend(self):
        """Invalid display backend should raise VmError."""
        config = VmConfig(name="test", display="wayland")
        with self.assertRaises(VmError) as cm:
            config.validate()
        self.assertIn("display must be one of", str(cm.exception))

    def test_invalid_vm_name(self):
        """Invalid VM name should raise VmError."""
        config = VmConfig(name="invalid name!")
        with self.assertRaises(VmError) as cm:
            config.validate()
        self.assertIn("invalid VM name", str(cm.exception))

    def test_valid_memory_sizes(self):
        """All valid memory sizes should pass validation."""
        for mem in MACHINE_BY_MEMORY_GB:
            config = VmConfig(name="test", memory_gb=mem)
            config.validate()  # Should not raise

    def test_valid_resolutions(self):
        """Valid resolutions should pass validation."""
        for res in ["1920x1080", "1280x720", "1366x768", "3840x2160"]:
            config = VmConfig(name="test", resolution=res)
            config.validate()  # Should not raise

    def test_valid_display_backends(self):
        """All valid display backends should pass validation."""
        for disp in DISPLAY_BACKENDS:
            config = VmConfig(name="test", display=disp)
            config.validate()  # Should not raise

    def test_missing_required_fields(self):
        """Config with missing name should fail (dataclass default prevents this)."""
        # Name is required by dataclass, but empty string should fail pattern
        config = VmConfig(name="")
        with self.assertRaises(VmError):
            config.validate()

    def test_machine_type_property(self):
        """machine_type should map memory to correct QEMU machine."""
        for mem, machine in MACHINE_BY_MEMORY_GB.items():
            config = VmConfig(name="test", memory_gb=mem)
            self.assertEqual(config.machine_type, machine)

    def test_kernel_path_property(self):
        """kernel_path should return the configured kernel when set."""
        config = VmConfig(name="test", kernel="/custom/kernel8.img")
        self.assertEqual(config.kernel_path, Path("/custom/kernel8.img"))


# ════════════════════════════════════════════════════════════════════════
# Command line argument parsing tests
# ════════════════════════════════════════════════════════════════════════

class TestCommandLine(unittest.TestCase):
    """Tests for CLI argument parsing and --help output."""

    def setUp(self):
        self.parser = build_parser()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_cli(self, argv, store_root=None):
        """Run main() with fake store so nothing touches real VM store."""
        from lobstervm.config import VmStore
        store = VmStore(store_root or self.tmpdir)
        # Patch VmStore in cli module
        import lobstervm.cli as cli_module
        orig_store = cli_module.VmStore
        cli_module.VmStore = lambda: store
        try:
            return main(argv)
        finally:
            cli_module.VmStore = orig_store

    def test_help_main(self):
        """Main --help should exit 0."""
        with self.assertRaises(SystemExit) as cm:
            self._run_cli(["--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_help_subcommands(self):
        """Every subcommand's --help should exit 0."""
        subcommands = [
            "list", "doctor", "gui", "create", "modify",
            "show", "delete", "start", "stop", "reset",
            "pause", "resume", "factory-reset", "snapshot",
            "screenshot", "click", "move", "wheel", "drag", "key",
            "type", "shell", "hmp", "serial"
        ]
        for cmd in subcommands:
            with self.subTest(cmd=cmd):
                with self.assertRaises(SystemExit) as cm:
                    self._run_cli([cmd, "--help"])
                self.assertEqual(cm.exception.code, 0)

    def test_create_parsing(self):
        """Create command should parse all config options."""
        # Just test parsing, not execution (would need kernel/disk)
        args = self.parser.parse_args([
            "create", "testvm",
            "--memory", "8",
            "--resolution", "1280x720",
            "--display", "vnc",
            "--audio", "off",
            "--share", "/tmp/share",
            "--kernel", "/custom/kernel8.img",
            "--description", "test vm"
        ])
        self.assertEqual(args.command, "create")
        self.assertEqual(args.name, "testvm")
        self.assertEqual(args.memory_gb, 8)
        self.assertEqual(args.resolution, "1280x720")
        self.assertEqual(args.display, "vnc")
        self.assertEqual(args.audio, "off")
        self.assertEqual(args.shared_folder, "/tmp/share")
        self.assertEqual(args.kernel, "/custom/kernel8.img")
        self.assertEqual(args.description, "test vm")

    def test_modify_parsing(self):
        """Modify command should parse config options."""
        args = self.parser.parse_args(["modify", "testvm", "--memory", "2"])
        self.assertEqual(args.command, "modify")
        self.assertEqual(args.name, "testvm")
        self.assertEqual(args.memory_gb, 2)

    def test_show_parsing(self):
        """Show command should accept VM name."""
        args = self.parser.parse_args(["show", "myvm"])
        self.assertEqual(args.command, "show")
        self.assertEqual(args.name, "myvm")

    def test_delete_parsing(self):
        """Delete command should accept VM name."""
        args = self.parser.parse_args(["delete", "myvm"])
        self.assertEqual(args.command, "delete")
        self.assertEqual(args.name, "myvm")

    def test_start_parsing(self):
        """Start command should parse all options."""
        args = self.parser.parse_args([
            "start", "myvm",
            "--headless",
            "--display", "none",
            "--resume", "snap1",
            "--wait"
        ])
        self.assertEqual(args.command, "start")
        self.assertEqual(args.name, "myvm")
        self.assertTrue(args.headless)
        self.assertEqual(args.display, "none")
        self.assertEqual(args.resume, "snap1")
        self.assertTrue(args.wait)

    def test_stop_parsing(self):
        """Stop command should parse force flag."""
        args = self.parser.parse_args(["stop", "myvm", "--force"])
        self.assertEqual(args.command, "stop")
        self.assertEqual(args.name, "myvm")
        self.assertTrue(args.force)

    def test_snapshot_parsing(self):
        """Snapshot command should parse action and tag."""
        for action in ["take", "restore", "list", "delete"]:
            args = self.parser.parse_args(["snapshot", "myvm", action, "mytag"])
            self.assertEqual(args.name, "myvm")
            self.assertEqual(args.action, action)
            self.assertEqual(args.tag, "mytag")
        # list without tag
        args = self.parser.parse_args(["snapshot", "myvm", "list"])
        self.assertEqual(args.action, "list")
        self.assertIsNone(args.tag)

    def test_screenshot_parsing(self):
        """Screenshot command should accept path."""
        args = self.parser.parse_args(["screenshot", "myvm", "/tmp/out.png"])
        self.assertEqual(args.name, "myvm")
        self.assertEqual(args.path, "/tmp/out.png")

    def test_click_parsing(self):
        """Click command should parse coordinates and flags."""
        args = self.parser.parse_args(["click", "myvm", "100", "200", "--double", "--right"])
        self.assertEqual(args.name, "myvm")
        self.assertEqual(args.x, 100)
        self.assertEqual(args.y, 200)
        self.assertTrue(args.double)
        self.assertTrue(args.right)

    def test_move_parsing(self):
        """Move command should parse coordinates."""
        args = self.parser.parse_args(["move", "myvm", "50", "60"])
        self.assertEqual(args.name, "myvm")
        self.assertEqual(args.x, 50)
        self.assertEqual(args.y, 60)

    def test_wheel_parsing_negative_dy(self):
        """Wheel accepts a negative notch without a '--' separator."""
        args = self.parser.parse_args(["wheel", "myvm", "-3"])
        self.assertEqual(args.name, "myvm")
        self.assertEqual(args.dy, -3)
        self.assertEqual(args.dx, 0)

    def test_wheel_parsing_negative_dx(self):
        """--dx may be negative too (horizontal scroll left)."""
        args = self.parser.parse_args(["wheel", "myvm", "2", "--dx", "-1"])
        self.assertEqual(args.dy, 2)
        self.assertEqual(args.dx, -1)

    def test_wheel_negative_notches_reach_the_monitor(self):
        """cmd_wheel turns -3 into the HMP command 'lwheel -3 0'."""
        VmStore(self.tmpdir).create(VmConfig(name="vm1"))
        with patch.object(Machine, "hmp") as hmp:
            self.assertEqual(self._run_cli(["wheel", "vm1", "-3"]), 0)
        hmp.assert_called_once_with("lwheel -3 0")

    def test_wheel_horizontal_reaches_the_monitor(self):
        """cmd_wheel turns 2 --dx -1 into 'lwheel 2 -1'."""
        VmStore(self.tmpdir).create(VmConfig(name="vm1"))
        with patch.object(Machine, "hmp") as hmp:
            self.assertEqual(
                self._run_cli(["wheel", "vm1", "2", "--dx", "-1"]), 0)
        hmp.assert_called_once_with("lwheel 2 -1")

    def test_host_compile_option_reaches_the_config(self):
        """--host-compile sets the setting, and '' clears it again."""
        args = self.parser.parse_args(["modify", "myvm", "--host-compile", "/libc"])
        config = VmConfig(name="myvm")
        _apply_config_options(config, args)
        self.assertEqual(config.host_compile_libc, "/libc")
        off = self.parser.parse_args(["modify", "myvm", "--host-compile", ""])
        _apply_config_options(config, off)
        self.assertEqual(config.host_compile_libc, "")

    def test_drag_parsing(self):
        """Drag command should parse four coordinates."""
        args = self.parser.parse_args(["drag", "myvm", "0", "0", "100", "100"])
        self.assertEqual(args.name, "myvm")
        self.assertEqual(args.x0, 0)
        self.assertEqual(args.y0, 0)
        self.assertEqual(args.x1, 100)
        self.assertEqual(args.y1, 100)

    def test_key_parsing(self):
        """Key command should parse scancode and mods."""
        args = self.parser.parse_args(["key", "myvm", "0x1C", "ctrl", "shift"])
        self.assertEqual(args.name, "myvm")
        self.assertEqual(args.scancode, "0x1C")
        self.assertEqual(args.mods, ["ctrl", "shift"])

    def test_type_parsing(self):
        """Type command should accept text."""
        args = self.parser.parse_args(["type", "myvm", "hello world"])
        self.assertEqual(args.name, "myvm")
        self.assertEqual(args.text, "hello world")

    def test_shell_parsing(self):
        """Shell command should parse command and timeout."""
        # Use a simple command without flags to avoid parsing ambiguity
        args = self.parser.parse_args(["shell", "myvm", "uptime", "--timeout", "30"])
        self.assertEqual(args.name, "myvm")
        self.assertEqual(args.command, ["uptime"])
        self.assertEqual(args.timeout, 30.0)

    def test_hmp_parsing(self):
        """HMP command should parse raw command."""
        args = self.parser.parse_args(["hmp", "myvm", "info", "status"])
        self.assertEqual(args.name, "myvm")
        self.assertEqual(args.command, ["info", "status"])

    def test_serial_parsing(self):
        """Serial command should parse lines and follow."""
        args = self.parser.parse_args(["serial", "-n", "20", "-f"])
        self.assertEqual(args.lines, 20)
        self.assertTrue(args.follow)

    def test_doctor_parsing(self):
        """Doctor command has no args."""
        args = self.parser.parse_args(["doctor"])
        self.assertEqual(args.command, "doctor")

    def test_default_vm_name(self):
        """Commands should default to 'lobsteros' when name omitted."""
        # Only commands that don't require additional required positional args
        for cmd in ["show", "delete", "start", "stop", "reset", "pause",
                     "resume", "factory-reset", "serial"]:
            args = self.parser.parse_args([cmd])
            self.assertEqual(args.name, "lobsteros", f"cmd={cmd}")

    def test_invalid_memory_choice(self):
        """Invalid memory should be rejected by argparse."""
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["create", "test", "--memory", "3"])

    def test_invalid_display_choice(self):
        """Invalid display should be rejected by argparse."""
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["create", "test", "--display", "wayland"])


# ════════════════════════════════════════════════════════════════════════
# HMP parsing helper tests
# ════════════════════════════════════════════════════════════════════════

class TestHMPParsing(unittest.TestCase):
    """Tests for HMP parsing helpers in machine.py."""

    def test_strip_hmp_echo_simple(self):
        """_strip_hmp_echo should drop command echo and prompt."""
        reply = b"command\noutput line 1\noutput line 2\n(qemu) "
        result = _strip_hmp_echo(reply)
        self.assertEqual(result, "output line 1\noutput line 2\n")

    def test_strip_hmp_echo_with_carriage_returns(self):
        """Should handle \\r characters."""
        reply = b"cmd\r\nline1\r\nline2\r\n(qemu) "
        result = _strip_hmp_echo(reply)
        self.assertEqual(result, "line1\nline2\n")

    def test_strip_hmp_echo_no_prompt(self):
        """Should handle reply without trailing prompt."""
        reply = b"cmd\noutput"
        result = _strip_hmp_echo(reply)
        self.assertEqual(result, "output")

    def test_strip_hmp_echo_empty(self):
        """Empty reply should return empty string."""
        reply = b"(qemu) "
        result = _strip_hmp_echo(reply)
        self.assertEqual(result, "")

    def test_parse_snapshot_table(self):
        """_parse_snapshot_table should extract snapshot tags."""
        table = """ ID        TAG                 VM SIZE                DATE       VM CLOCK
 0        base                1.2G                2024-01-01 12:00:00   00:00:00.000
 1        snap1               2.3G                2024-01-02 13:00:00   00:01:30.000
--        current             2.3G                2024-01-02 13:00:00   00:01:30.000"""
        tags = _parse_snapshot_table(table)
        self.assertEqual(tags, ["base", "snap1", "current"])

    def test_parse_snapshot_table_empty(self):
        """Empty table should return empty list."""
        self.assertEqual(_parse_snapshot_table(""), [])
        self.assertEqual(_parse_snapshot_table("no snapshots"), [])

    def test_parse_snapshot_table_no_valid_lines(self):
        """Lines without numeric ID should be skipped."""
        table = """Snapshot list is empty"""
        self.assertEqual(_parse_snapshot_table(table), [])

    def test_read_until_prompt(self):
        """_read_until_prompt should read until HMP_PROMPT."""
        mock_sock = Mock()
        mock_sock.recv.side_effect = [
            b"some output\n(qemu) ",
            b"",  # subsequent calls return empty
        ]
        mock_sock.settimeout = Mock()
        result = _read_until_prompt(mock_sock)
        self.assertTrue(result.endswith(b"(qemu) "))

    def test_read_until_prompt_timeout(self):
        """Should raise VmError on socket timeout."""
        mock_sock = Mock()
        mock_sock.recv.side_effect = socket.timeout("timeout")
        mock_sock.settimeout = Mock()
        with self.assertRaises(Exception) as cm:
            _read_until_prompt(mock_sock)
        self.assertIn("QEMU monitor did not answer", str(cm.exception))

    def test_shell_output(self):
        """_shell_output should extract text between command and prompt."""
        raw = b"command\necho output line 1\necho output line 2\nlobster:~# "
        result = _shell_output(raw, "command")
        self.assertEqual(result, "echo output line 1\necho output line 2")

    def test_shell_output_with_carriage_returns(self):
        """Should handle \\r in output."""
        raw = b"cmd\r\nline1\r\nline2\r\nlobster:~# "
        result = _shell_output(raw, "cmd")
        self.assertEqual(result, "line1\nline2")

    def test_shell_output_command_not_found(self):
        """Should return empty when command not in output."""
        raw = b"unrelated output\nlobster:~# "
        result = _shell_output(raw, "command")
        self.assertEqual(result, "")


# ════════════════════════════════════════════════════════════════════════
# VmStore tests
# ════════════════════════════════════════════════════════════════════════

class TestVmStore(unittest.TestCase):
    """Tests for VmStore operations."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = VmStore(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_list_names_empty(self):
        """Empty store should return empty list."""
        self.assertEqual(self.store.list_names(), [])

    def test_create_and_load(self):
        """Create and load a VM config."""
        config = VmConfig(name="testvm", memory_gb=4)
        self.store.create(config)
        loaded = self.store.load("testvm")
        self.assertEqual(loaded.name, "testvm")
        self.assertEqual(loaded.memory_gb, 4)

    def test_create_duplicate_fails(self):
        """Creating duplicate VM should raise VmError."""
        config = VmConfig(name="testvm")
        self.store.create(config)
        with self.assertRaises(VmError):
            self.store.create(config)

    def test_vm_json_without_host_compile_loads(self):
        """A vm.json written before --host-compile existed still loads."""
        with tempfile.TemporaryDirectory() as tmp:
            legacy = VmStore(tmp)
            legacy.root.mkdir(parents=True, exist_ok=True)
            vm_dir = legacy.vm_dir("legacy")
            vm_dir.mkdir(parents=True)
            (vm_dir / "vm.json").write_text(json.dumps({
                "name": "legacy",
                "memory_gb": 4,
                "resolution": "1920x1080",
                "display": "none",
                "audio": True,
                "shared_folder": "",
                "kernel": "",
                "description": "",
                "extra_args": [],
                "accel": "tcg",
            }))
            loaded = legacy.load("legacy")
            self.assertEqual(loaded.name, "legacy")
            self.assertEqual(loaded.host_compile_libc, "")
            loaded.validate()

    def test_delete_vm(self):
        """Delete should remove VM directory."""
        config = VmConfig(name="testvm")
        self.store.create(config)
        self.store.delete("testvm")
        self.assertFalse(self.store.exists("testvm"))

    def test_delete_nonexistent_fails(self):
        """Deleting nonexistent VM should raise VmError."""
        with self.assertRaises(VmError):
            self.store.delete("nonexistent")

    def test_reset_disk_creates_overlay(self):
        """reset_disk should create qcow2 overlay on base image."""
        config = VmConfig(name="testvm")
        self.store.create(config)
        disk = self.store.disk_path("testvm")
        self.assertTrue(disk.exists())
        # Verify it's a qcow2 file (basic check)
        self.assertTrue(disk.stat().st_size > 0)

    def test_vm_dir(self):
        """vm_dir should return correct path."""
        path = self.store.vm_dir("testvm")
        self.assertEqual(path, Path(self.tmpdir) / "testvm")

    def test_disk_path(self):
        """disk_path should return correct path."""
        path = self.store.disk_path("testvm")
        self.assertEqual(path, Path(self.tmpdir) / "testvm" / "disk.qcow2")


# ════════════════════════════════════════════════════════════════════════
# Paths module tests
# ════════════════════════════════════════════════════════════════════════

class TestPaths(unittest.TestCase):
    """Tests for paths module (smoke tests, may need env vars)."""

    def test_repo_root(self):
        """REPO_ROOT should be the repository root."""
        expected = Path(__file__).resolve().parent.parent
        self.assertEqual(paths.REPO_ROOT, expected)

    def test_data_home_default(self):
        """data_home should return XDG path or ~/.local/share/lobstervm."""
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(paths, "host_os", return_value=paths.LINUX), \
                patch.object(Path, "home", return_value=Path("/home/u")):
            home = paths.data_home()
            self.assertIn("lobstervm", str(home))

    def test_runtime_home_default(self):
        """runtime_home should return XDG_RUNTIME_DIR or /tmp/lobstervm-UID."""
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000"}, clear=True), \
                patch.object(paths, "host_os", return_value=paths.LINUX):
            runtime = paths.runtime_home()
            self.assertIn("lobstervm", str(runtime))

    def test_qemu_binary_name(self):
        """QEMU binary name should be correct."""
        self.assertEqual(paths.QEMU_BASENAME, "qemu-system-aarch64")


class TestEnvOverrides(unittest.TestCase):
    """A set LOBSTER_* override is authoritative: it is used or it fails loudly."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _bundle_kernel(self):
        kernel = self.root / "bundle" / "share" / "lobsteros" / "kernel8.img"
        kernel.parent.mkdir(parents=True)
        kernel.write_bytes(b"bundled")
        return kernel

    def test_override_pointing_at_missing_file_raises_naming_variable(self):
        """A set-but-wrong override must not silently fall back."""
        self._bundle_kernel()
        with patch.dict(os.environ, {"LOBSTER_KERNEL": str(self.root / "typo.img")}), \
                patch.object(paths, "bundle_dirs", return_value=[self.root / "bundle"]):
            with self.assertRaises(paths.MissingComponent) as ctx:
                paths.default_kernel()
        self.assertIn("LOBSTER_KERNEL", str(ctx.exception))
        self.assertIn(str(self.root / "typo.img"), str(ctx.exception))
        self.assertNotIn("Looked in:", str(ctx.exception))

    def test_override_pointing_at_existing_file_is_used(self):
        """An existing override wins over a bundled copy."""
        override = self.root / "mine.img"
        override.write_bytes(b"mine")
        with patch.dict(os.environ, {"LOBSTER_KERNEL": str(override)}), \
                patch.object(paths, "bundle_dirs", return_value=[self.root / "bundle"]):
            self.assertEqual(paths.default_kernel(), override)

    def test_relative_override_error_names_absolute_path_and_cwd(self):
        """Relative overrides resolve against the cwd; the error must say where."""
        os.chdir(self.root)
        with patch.dict(os.environ, {"LOBSTER_KERNEL": "kernels/x.img"}):
            with self.assertRaises(paths.MissingComponent) as ctx:
                paths.default_kernel()
        message = str(ctx.exception)
        self.assertIn("LOBSTER_KERNEL", message)
        self.assertIn(str((self.root / "kernels" / "x.img").absolute()), message)
        self.assertIn("current directory", message)

    def test_empty_override_falls_back_to_search_order(self):
        """An empty variable counts as unset."""
        bundled = self._bundle_kernel()
        with patch.dict(os.environ, {"LOBSTER_KERNEL": ""}), \
                patch.object(paths, "bundle_dirs", return_value=[self.root / "bundle"]):
            self.assertEqual(paths.default_kernel(), bundled)

    def test_unset_override_falls_back_to_search_order(self):
        """With no override the bundled kernel is still found as before."""
        bundled = self._bundle_kernel()
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(paths, "bundle_dirs", return_value=[self.root / "bundle"]):
            self.assertEqual(paths.default_kernel(), bundled)

    def test_unset_override_missing_everywhere_lists_searched_paths(self):
        """Fallback failures keep the existing 'Looked in' diagnostics."""
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(paths, "bundle_dirs", return_value=[self.root / "none"]):
            with self.assertRaises(paths.MissingComponent) as ctx:
                paths.default_kernel()
        self.assertIn("Looked in:", str(ctx.exception))
        self.assertNotIn("LOBSTER_KERNEL=", str(ctx.exception))


# ════════════════════════════════════════════════════════════════════════
# Machine class tests (no QEMU)
# ════════════════════════════════════════════════════════════════════════

class TestMachine(unittest.TestCase):
    """Tests for Machine class that don't require QEMU."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = VmStore(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_machine_creation(self):
        """Machine should be constructible with valid name."""
        config = VmConfig(name="testvm")
        self.store.create(config)
        machine = Machine("testvm", self.store)
        self.assertEqual(machine.name, "testvm")
        self.assertEqual(machine.config.name, "testvm")

    def test_machine_nonexistent_fails(self):
        """Machine for nonexistent VM should raise VmError."""
        with self.assertRaises(VmError):
            Machine("nonexistent", self.store)

    def test_pid_not_running(self):
        """pid() should return None when VM not running."""
        config = VmConfig(name="testvm")
        self.store.create(config)
        machine = Machine("testvm", self.store)
        self.assertIsNone(machine.pid())

    def test_is_running_false(self):
        """is_running() should return False when stopped."""
        config = VmConfig(name="testvm")
        self.store.create(config)
        machine = Machine("testvm", self.store)
        self.assertFalse(machine.is_running())

    def test_status_stopped(self):
        """status() should return 'stopped' when not running."""
        config = VmConfig(name="testvm")
        self.store.create(config)
        machine = Machine("testvm", self.store)
        self.assertEqual(machine.status(), "stopped")

    def test_runtime_dir(self):
        """runtime_dir should be under runtime_home."""
        config = VmConfig(name="testvm")
        self.store.create(config)
        machine = Machine("testvm", self.store)
        self.assertIn("testvm", str(machine.runtime_dir))

    def test_monitor_path(self):
        """monitor_path should point to monitor.sock in runtime dir."""
        config = VmConfig(name="testvm")
        self.store.create(config)
        machine = Machine("testvm", self.store)
        self.assertEqual(machine.monitor_path.name, "monitor.sock")

    def test_serial_paths(self):
        """Serial paths should be correctly named."""
        config = VmConfig(name="testvm")
        self.store.create(config)
        machine = Machine("testvm", self.store)
        self.assertEqual(machine.serial.socket_path.name, "serial.sock")
        self.assertEqual(machine.serial_log_path.name, "serial.log")

    def test_serial_bytes_empty(self):
        """serial_bytes should return empty bytes when log doesn't exist."""
        config = VmConfig(name="testvm")
        self.store.create(config)
        machine = Machine("testvm", self.store)
        self.assertEqual(machine.serial_bytes(), b"")

    def test_vm_error_on_hmp_when_stopped(self):
        """hmp() should raise VmError when VM not running."""
        config = VmConfig(name="testvm")
        self.store.create(config)
        machine = Machine("testvm", self.store)
        with self.assertRaises(VmError):
            machine.hmp("info status")

    def test_mouse_wheel_sends_lwheel(self):
        """mouse_wheel sends both notches, dx defaulting to 0."""
        machine = self._running_machine()
        with patch.object(Machine, "hmp") as hmp:
            machine.mouse_wheel(-3)
        hmp.assert_called_once_with("lwheel -3 0")

    def test_mouse_wheel_sends_horizontal_notch(self):
        """mouse_wheel forwards dx as well."""
        machine = self._running_machine()
        with patch.object(Machine, "hmp") as hmp:
            machine.mouse_wheel(2, -1)
        hmp.assert_called_once_with("lwheel 2 -1")

    def _running_machine(self):
        """A Machine whose VM looks running and whose serial log is a file."""
        self.store.create(VmConfig(name="testvm"))
        machine = Machine("testvm", self.store)
        machine.runtime_dir.mkdir(parents=True, exist_ok=True)
        patcher = patch.object(Machine, "is_running", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, machine.runtime_dir, True)
        return machine

    def test_send_line_appends_newline_without_waiting(self):
        """send_line should type text + newline and return immediately."""
        machine = self._running_machine()
        with patch.object(Machine, "type_serial") as typed:
            machine.send_line("run /sd/guiapp")
        typed.assert_called_once_with("run /sd/guiapp\n")

    def test_wait_serial_returns_offset_past_marker(self):
        """wait_serial should return the offset just after the marker."""
        machine = self._running_machine()
        machine.serial_log_path.write_bytes(b"boot... desktop-ready t_ms=5")
        end = machine.wait_serial(b"desktop-ready", timeout=1.0)
        self.assertEqual(end, len(b"boot... desktop-ready"))

    def test_wait_serial_ignores_matches_before_offset(self):
        """A marker seen before since_offset must not satisfy the wait."""
        machine = self._running_machine()
        machine.serial_log_path.write_bytes(b"Uptime: 1\n")
        with self.assertRaises(VmError):
            machine.wait_serial(b"Uptime:", timeout=0.3, since_offset=5)

    def test_wait_serial_times_out(self):
        """wait_serial should raise VmError when the marker never appears."""
        machine = self._running_machine()
        with self.assertRaises(VmError) as ctx:
            machine.wait_serial(b"never", timeout=0.3)
        self.assertIn("timed out", str(ctx.exception))

    def test_wait_serial_stops_when_vm_exits(self):
        """wait_serial should fail fast if the VM is not running."""
        machine = self._running_machine()
        with patch.object(Machine, "is_running", return_value=False):
            with self.assertRaises(VmError) as ctx:
                machine.wait_serial(b"never", timeout=30.0)
        self.assertIn("exited", str(ctx.exception))

    def test_shell_waits_for_prompt_count_to_grow(self):
        """shell() should return only after a new prompt follows the command."""
        machine = self._running_machine()
        machine.serial_log_path.write_bytes(b"lobster:/# ")

        def guest_replies(_text):
            machine.serial_log_path.write_bytes(
                b"lobster:/# uptime\r\nUptime: 3s\r\nlobster:/# ")

        with patch.object(Machine, "type_serial", side_effect=guest_replies):
            output = machine.shell("uptime", timeout=2.0)
        self.assertEqual(output, "Uptime: 3s")

    def test_shell_times_out_while_foreground_app_owns_console(self):
        """No new prompt (foreground app running) must raise, not hang."""
        machine = self._running_machine()
        machine.serial_log_path.write_bytes(b"lobster:/# ")
        with patch.object(Machine, "type_serial"):
            with self.assertRaises(VmError):
                machine.shell("run /sd/guiapp", timeout=0.4)


# ════════════════════════════════════════════════════════════════════════
# Test runner
# ════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)