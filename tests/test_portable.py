"""Platform branches: paths, channels, process control, display, bundle layout."""
import ctypes
import json
import os
import signal
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lobstervm import channel, display, firstrun, paths, procctl
from lobstervm.config import VmConfig, VmError, VmStore
from lobstervm.machine import Machine

FAKE_SIGKILL = 9
PLATFORMS = {"windows": "win32", "macos": "darwin", "linux": "linux"}


def on_platform(name):
    return patch.object(sys, "platform", PLATFORMS[name])


class TempDirCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name).resolve()


class TestHostOs(unittest.TestCase):
    def test_each_platform_maps_to_its_name(self):
        for name, value in PLATFORMS.items():
            with patch.object(sys, "platform", value):
                self.assertEqual(paths.host_os(), name)

    def test_unknown_posix_counts_as_linux(self):
        with patch.object(sys, "platform", "freebsd14"):
            self.assertEqual(paths.host_os(), paths.LINUX)

    def test_exe_suffix_only_on_windows(self):
        with on_platform("windows"):
            self.assertEqual(paths.exe_name("qemu-img"), "qemu-img.exe")
        with on_platform("linux"):
            self.assertEqual(paths.exe_name("qemu-img"), "qemu-img")


class TestDirectories(unittest.TestCase):
    def dirs(self, name, env):
        with on_platform(name), patch.dict(os.environ, env, clear=True):
            return paths.data_home(), paths.runtime_home()

    def test_linux_uses_xdg(self):
        data, run = self.dirs("linux", {"XDG_DATA_HOME": "/x/data",
                                        "XDG_RUNTIME_DIR": "/x/run"})
        self.assertEqual(data, Path("/x/data/lobstervm"))
        self.assertEqual(run, Path("/x/run/lobstervm"))

    def test_linux_without_xdg_falls_back_to_home_and_tmp(self):
        with patch.object(Path, "home", return_value=Path("/home/u")), \
                patch.object(os, "getuid", create=True, return_value=1000):
            data, run = self.dirs("linux", {})
        self.assertEqual(data, Path("/home/u/.local/share/lobstervm"))
        self.assertEqual(run.parent, Path(tempfile.gettempdir()))

    def test_macos_uses_application_support_and_short_runtime(self):
        with patch.object(Path, "home", return_value=Path("/Users/u")), \
                patch.object(os, "getuid", create=True, return_value=501):
            data, run = self.dirs("macos", {})
        self.assertEqual(data, Path("/Users/u/Library/Application Support/LobsterVM"))
        self.assertTrue(run.name.startswith("lobstervm-"))
        self.assertLess(len(str(run / "t" / "monitor.sock")), channel.UNIX_PATH_LIMIT)

    def test_windows_uses_localappdata(self):
        data, run = self.dirs("windows", {"LOCALAPPDATA": "C:\\L"})
        self.assertEqual(data, Path("C:\\L") / "LobsterVM")
        self.assertEqual(run, Path("C:\\L") / "LobsterVM" / "run")

    def test_windows_without_localappdata_uses_profile(self):
        with patch.object(Path, "home", return_value=Path("C:\\Users\\u")):
            data, _ = self.dirs("windows", {})
        self.assertEqual(data, Path("C:\\Users\\u") / "AppData" / "Local" / "LobsterVM")

    def test_overrides_win_on_every_platform(self):
        env = {"LOBSTERVM_HOME": "/o/home", "LOBSTERVM_RUNTIME": "/o/run"}
        for name in PLATFORMS:
            self.assertEqual(self.dirs(name, env), (Path("/o/home"), Path("/o/run")))


class TestBundleDiscovery(TempDirCase):
    def frozen(self, executable):
        return patch.multiple(sys, frozen=True, executable=str(executable), create=True)

    def test_not_frozen_has_no_bundle_dirs(self):
        self.assertEqual(paths.bundle_dirs(), [])

    def test_frozen_looks_next_to_the_executable(self):
        with self.frozen(self.tmp / "lobstervm"):
            self.assertEqual(paths.bundle_dirs()[0], self.tmp)

    def test_macos_app_adds_resources_dir(self):
        exe = self.tmp / "LobsterVM.app" / "Contents" / "MacOS" / "LobsterVM"
        with self.frozen(exe):
            dirs = paths.bundle_dirs()
        self.assertIn(exe.parent.parent / "Resources", dirs)

    def test_bundle_component_found_after_env_but_before_checkout(self):
        kernel = self.tmp / "share" / "lobsteros" / "kernel8.img"
        kernel.parent.mkdir(parents=True)
        kernel.write_bytes(b"k")
        with self.frozen(self.tmp / "lobstervm"), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(paths.default_kernel(), kernel)
            self.assertTrue(paths.is_bundled(kernel))

    def test_env_beats_bundle(self):
        bundled = self.tmp / "share" / "lobsteros" / "kernel8.img"
        bundled.parent.mkdir(parents=True)
        bundled.write_bytes(b"k")
        override = self.tmp / "other.img"
        override.write_bytes(b"o")
        with self.frozen(self.tmp / "lobstervm"), \
                patch.dict(os.environ, {"LOBSTER_KERNEL": str(override)}):
            self.assertEqual(paths.default_kernel(), override)

    def test_missing_component_error_lists_every_place_looked(self):
        with patch.dict(os.environ, {"LOBSTER_DTB": str(self.tmp / "no.dtb")}), \
                patch.object(paths, "REPO_ROOT", self.tmp):
            with self.assertRaises(paths.MissingComponent) as caught:
                paths.default_dtb()
        self.assertIn(str(self.tmp / "no.dtb"), str(caught.exception))
        self.assertIn("Looked in", str(caught.exception))

    def test_repo_file_is_not_bundled(self):
        self.assertFalse(paths.is_bundled(paths.REPO_ROOT / "kernel8.img"))

    def test_windows_bundle_uses_exe_names(self):
        qemu = self.tmp / "qemu" / "bin" / "qemu-system-aarch64.exe"
        qemu.parent.mkdir(parents=True)
        qemu.write_bytes(b"")
        with on_platform("windows"), self.frozen(self.tmp / "lobstervm.exe"), \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(Path, "home", return_value=self.tmp), \
                patch.object(paths, "_which", return_value=None):
            self.assertEqual(paths.qemu_binary(), qemu)


class TestEndpointAndChannel(TempDirCase):
    def test_unix_endpoint_options(self):
        endpoint = channel.Endpoint(path="/r/a,b.sock")
        self.assertEqual(endpoint.chardev_options(), "path=/r/a,,b.sock,server=on,wait=off")

    def test_tcp_endpoint_options(self):
        options = channel.Endpoint(port=4321).chardev_options()
        self.assertEqual(options, "host=127.0.0.1,port=4321,server=on,wait=off")

    def test_free_port_is_bindable_loopback_port(self):
        port = channel.free_port()
        self.assertGreater(port, 0)
        with socket.socket() as probe:
            probe.bind((channel.LOOPBACK, port))

    def test_posix_channel_uses_unix_socket(self):
        with on_platform("linux"), patch.dict(os.environ, {}, clear=True):
            endpoint = channel.Channel(self.tmp, "monitor").allocate()
        self.assertFalse(endpoint.is_tcp)
        self.assertEqual(endpoint.path, str(self.tmp / "monitor.sock"))

    def test_windows_channel_uses_tcp_and_records_port(self):
        chan = channel.Channel(self.tmp, "serial")
        with on_platform("windows"):
            endpoint = chan.allocate()
        self.assertTrue(endpoint.is_tcp)
        self.assertEqual(json.loads(chan.port_path.read_text())["port"], endpoint.port)
        self.assertEqual(chan.locate(), endpoint)

    def test_env_switch_forces_tcp_on_linux(self):
        with on_platform("linux"), patch.dict(os.environ, {channel.FORCE_TCP_ENV: "1"}):
            self.assertTrue(channel.Channel(self.tmp, "monitor").allocate().is_tcp)

    def test_overlong_unix_path_falls_back_to_tcp(self):
        deep = self.tmp / ("d" * channel.UNIX_PATH_LIMIT)
        deep.mkdir()
        with on_platform("macos"), patch.dict(os.environ, {}, clear=True):
            self.assertTrue(channel.Channel(deep, "monitor").allocate().is_tcp)

    def test_allocate_clears_stale_port_file(self):
        chan = channel.Channel(self.tmp, "monitor")
        chan.port_path.write_text('{"port": 1}')
        with on_platform("linux"), patch.dict(os.environ, {}, clear=True):
            self.assertFalse(chan.allocate().is_tcp)
        self.assertFalse(chan.port_path.exists())

    def test_locate_without_port_file_is_the_unix_socket(self):
        endpoint = channel.Channel(self.tmp, "monitor").locate()
        self.assertEqual(endpoint.path, str(self.tmp / "monitor.sock"))

    def test_locate_rejects_corrupt_port_file(self):
        chan = channel.Channel(self.tmp, "monitor")
        chan.port_path.write_text("not json")
        with self.assertRaises(VmError):
            chan.locate()

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_restrict_makes_socket_owner_only(self):
        chan = channel.Channel(self.tmp, "monitor")
        chan.socket_path.write_bytes(b"")
        chan.restrict()
        self.assertEqual(chan.socket_path.stat().st_mode & 0o777, 0o600)

    def test_tcp_client_round_trip(self):
        with socket.socket() as server:
            server.bind((channel.LOOPBACK, 0))
            server.listen(1)
            endpoint = channel.Endpoint(port=server.getsockname()[1])
            with endpoint.connect() as client:
                peer, _ = server.accept()
                client.sendall(b"hi")
                self.assertEqual(peer.recv(2), b"hi")
                peer.close()

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "needs Unix sockets")
    def test_unix_client_round_trip(self):
        path = str(self.tmp / "s.sock")
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(path)
            server.listen(1)
            with channel.Endpoint(path=path).connect() as client:
                peer, _ = server.accept()
                client.sendall(b"yo")
                self.assertEqual(peer.recv(2), b"yo")
                peer.close()

    def test_send_waits_until_the_server_has_read_everything(self):
        received = bytearray()
        with socket.socket() as server:
            server.bind((channel.LOOPBACK, 0))
            server.listen(1)
            endpoint = channel.Endpoint(port=server.getsockname()[1])

            def slow_reader():
                peer, _ = server.accept()
                with peer:
                    while chunk := peer.recv(4):
                        received.extend(chunk)
                        time.sleep(0.02)

            thread = threading.Thread(target=slow_reader)
            thread.start()
            endpoint.send(b"print(sum(i*i for i in range(10)))\n", drain_timeout=5)
            thread.join()
        self.assertEqual(bytes(received), b"print(sum(i*i for i in range(10)))\n")

    def test_send_gives_up_when_the_server_never_reads(self):
        with socket.socket() as server:
            server.bind((channel.LOOPBACK, 0))
            server.listen(1)
            endpoint = channel.Endpoint(port=server.getsockname()[1])
            started = time.monotonic()
            endpoint.send(b"x", drain_timeout=0.3)
            self.assertLess(time.monotonic() - started, 3)

    def test_connect_waits_for_a_late_server(self):
        port = channel.free_port()
        ready = threading.Event()

        def serve():
            ready.wait()
            with socket.socket() as server:
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind((channel.LOOPBACK, port))
                server.listen(1)
                server.accept()[0].close()

        thread = threading.Thread(target=serve)
        thread.start()
        threading.Timer(0.3, ready.set).start()
        channel.Endpoint(port=port).connect(timeout=5).close()
        thread.join()

    def test_connect_gives_up_with_vm_error(self):
        with self.assertRaises(VmError):
            channel.Endpoint(port=channel.free_port()).connect(timeout=0.2)

    def test_is_ready_false_without_server(self):
        self.assertFalse(channel.Channel(self.tmp, "monitor").is_ready())


class TestProcessControl(unittest.TestCase):
    def test_posix_detach_starts_new_session(self):
        with on_platform("linux"):
            self.assertEqual(procctl.detach_options(True), {"start_new_session": True})

    def test_windows_detach_flags(self):
        with on_platform("windows"):
            gui = procctl.detach_options(False)["creationflags"]
            headless = procctl.detach_options(True)["creationflags"]
        wanted = procctl.CREATE_NEW_PROCESS_GROUP | procctl.DETACHED_PROCESS
        self.assertEqual(gui, wanted)
        self.assertEqual(headless, wanted | procctl.CREATE_NO_WINDOW)

    def fake_kernel32(self, exit_code, opened=True):
        kernel32 = MagicMock()
        kernel32.OpenProcess.return_value = 7 if opened else 0

        def get_exit_code(handle, pointer):
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_ulong))[0] = exit_code
            return 1

        kernel32.GetExitCodeProcess.side_effect = get_exit_code
        return kernel32

    def test_windows_alive_when_still_active(self):
        kernel32 = self.fake_kernel32(procctl.STILL_ACTIVE)
        with on_platform("windows"), patch.object(procctl, "_kernel32", return_value=kernel32):
            self.assertTrue(procctl.is_alive(42))
        kernel32.CloseHandle.assert_called_once_with(7)

    def test_windows_dead_when_exited(self):
        with on_platform("windows"), \
                patch.object(procctl, "_kernel32", return_value=self.fake_kernel32(0)):
            self.assertFalse(procctl.is_alive(42))

    def test_windows_dead_when_pid_cannot_be_opened(self):
        with on_platform("windows"), patch.object(
                procctl, "_kernel32", return_value=self.fake_kernel32(0, opened=False)):
            self.assertFalse(procctl.is_alive(42))

    def test_windows_force_kill_terminates_process(self):
        kernel32 = self.fake_kernel32(0)
        with on_platform("windows"), patch.object(procctl, "_kernel32", return_value=kernel32):
            procctl.force_kill(42)
        kernel32.TerminateProcess.assert_called_once_with(7, procctl.FORCED_EXIT_CODE)

    def test_posix_force_kill_sends_sigkill(self):
        with on_platform("linux"), patch.object(os, "kill") as kill, \
                patch.object(signal, "SIGKILL", FAKE_SIGKILL, create=True):
            procctl.force_kill(42)
        kill.assert_called_once_with(42, FAKE_SIGKILL)

    def test_posix_alive_semantics(self):
        with on_platform("linux"):
            with patch.object(os, "kill") as kill:
                self.assertTrue(procctl.is_alive(42))
            kill.assert_called_once_with(42, 0)
            with patch.object(os, "kill", side_effect=ProcessLookupError):
                self.assertFalse(procctl.is_alive(42))
            with patch.object(os, "kill", side_effect=PermissionError):
                self.assertTrue(procctl.is_alive(42))

    def test_windows_qemu_detection_uses_image_name(self):
        with on_platform("windows"), \
                patch.object(procctl, "_windows_image_name",
                             return_value="C:\\q\\bin\\qemu-system-aarch64.exe"), \
                patch.object(procctl, "is_alive", return_value=True):
            self.assertTrue(procctl.is_qemu_process(42))
        with on_platform("windows"), \
                patch.object(procctl, "_windows_image_name", return_value="C:\\x\\cmd.exe"):
            self.assertFalse(procctl.is_qemu_process(42))

    def test_macos_qemu_detection_uses_ps(self):
        result = MagicMock(stdout="qemu-system-aarch64\n")
        with on_platform("macos"), patch.object(procctl.subprocess, "run", return_value=result):
            self.assertTrue(procctl.is_qemu_process(42))
        with on_platform("macos"), \
                patch.object(procctl.subprocess, "run", side_effect=OSError):
            self.assertFalse(procctl.is_qemu_process(42))

    @unittest.skipUnless(Path("/proc/self/stat").exists(), "needs procfs")
    def test_linux_qemu_detection_rejects_foreign_pid(self):
        with on_platform("linux"):
            self.assertFalse(procctl.is_qemu_process(os.getpid()))


class TestDisplay(unittest.TestCase):
    HELP = "Available display backend types:\ncocoa\nnone\nsdl\n"

    def test_parse_display_help_skips_header(self):
        self.assertEqual(display.parse_display_help(self.HELP), ["cocoa", "none", "sdl"])

    def test_auto_prefers_native_backend_per_os(self):
        wanted = {"macos": "cocoa", "windows": "sdl", "linux": "gtk"}
        for name, backend in wanted.items():
            with on_platform(name):
                self.assertEqual(display.pick_backend([]), backend)
                self.assertEqual(display.pick_backend(["cocoa", "sdl", "gtk"]), backend)

    def test_auto_falls_back_to_what_qemu_lists(self):
        with on_platform("linux"):
            self.assertEqual(display.pick_backend(["sdl", "none"]), "sdl")
            self.assertEqual(display.pick_backend(["none"]), "none")

    def test_explicit_backend_is_untouched(self):
        self.assertEqual(display.resolve_display("vnc", "/no/qemu"), "vnc")

    def test_auto_resolves_through_qemu_help(self):
        with on_platform("macos"), patch.object(display, "available_displays",
                                                return_value=["sdl"]):
            self.assertEqual(display.resolve_display("auto", "q"), "sdl")

    def test_missing_qemu_means_no_listing(self):
        self.assertEqual(display.available_displays("/no/such/qemu"), [])

    def test_display_argument_formats(self):
        self.assertTrue(display.display_argument("gtk").startswith("gtk,"))
        self.assertEqual(display.display_argument("vnc"), "vnc=127.0.0.1:9")
        self.assertEqual(display.display_argument("cocoa"), "cocoa")


class TestMachineChannels(TempDirCase):
    def setUp(self):
        super().setUp()
        fakes = {}
        for variable, name in (("LOBSTER_QEMU", "qemu-system-aarch64"),
                               ("LOBSTER_KERNEL", "kernel8.img")):
            (self.tmp / name).write_bytes(b"")
            fakes[variable] = str(self.tmp / name)
        (self.tmp / "base.img").write_bytes(b"")
        fakes["LOBSTER_BASE_DISK"] = str(self.tmp / "base.img")
        fakes["LOBSTERVM_RUNTIME"] = str(self.tmp / "run")
        env = patch.dict(os.environ, fakes)
        env.start()
        self.addCleanup(env.stop)
        self.store = VmStore(self.tmp / "vms")

    def machine(self):
        with patch("lobstervm.config.run_qemu_img"):
            self.store.create(VmConfig(name="vm"))
        return Machine("vm", self.store)

    def test_command_uses_socket_chardevs_and_logs_serial(self):
        machine = self.machine()
        machine._prepare_runtime_dir()
        command = machine._qemu_command("none", None)
        serial = command[command.index("chardev:ser0") - 1 - 1]
        self.assertIn("logfile=", serial)
        self.assertIn("logappend=on", serial)
        self.assertIn("chardev=mon0,mode=readline", command)
        self.assertNotIn("stdio", command)

    def test_command_with_forced_tcp_names_loopback_ports(self):
        machine = self.machine()
        with patch.dict(os.environ, {channel.FORCE_TCP_ENV: "1"}):
            machine._prepare_runtime_dir()
        joined = " ".join(machine._qemu_command("none", None))
        self.assertIn("host=127.0.0.1,port=", joined)
        self.assertNotIn("path=", joined)


class TestBaseImageCopy(TempDirCase):
    def test_bundled_base_is_copied_once_and_reused(self):
        bundled = self.tmp / "bundle" / "share" / "lobsteros" / "lobsteros.img"
        bundled.parent.mkdir(parents=True)
        bundled.write_bytes(b"image-bytes")
        store = VmStore(self.tmp / "vms")
        with patch.multiple(sys, frozen=True, create=True,
                            executable=str(self.tmp / "bundle" / "lobstervm")), \
                patch.dict(os.environ, {}, clear=True):
            first = store.backing_image()
            first_mtime = first.stat().st_mtime_ns
            second = store.backing_image()
        self.assertEqual(first, second)
        self.assertEqual(first.parent, store.root / "base")
        self.assertRegex(first.name, r"^lobsteros-[0-9a-f]{16}\.img$")
        self.assertEqual(first.read_bytes(), b"image-bytes")
        self.assertEqual(second.stat().st_mtime_ns, first_mtime)

    def test_checkout_base_is_used_in_place(self):
        base = self.tmp / "base.img"
        base.write_bytes(b"x")
        with patch.dict(os.environ, {"LOBSTER_BASE_DISK": str(base)}):
            self.assertEqual(VmStore(self.tmp / "vms").backing_image(), base)

    def test_base_is_a_reserved_vm_name(self):
        with self.assertRaises(VmError):
            VmConfig(name="base").validate()


class TestFirstRun(TempDirCase):
    def test_creates_default_vm_only_when_store_is_empty(self):
        store = VmStore(self.tmp)
        base = self.tmp / "base.img"
        base.write_bytes(b"")
        with patch("lobstervm.config.run_qemu_img"), \
                patch.dict(os.environ, {"LOBSTER_BASE_DISK": str(base)}):
            self.assertEqual(firstrun.ensure_default_vm(store), "LobsterOS")
            self.assertIsNone(firstrun.ensure_default_vm(store))
        config = store.load("LobsterOS")
        self.assertEqual((config.memory_gb, config.resolution, config.display),
                         (4, "1280x720", "auto"))

    def test_message_names_missing_component_and_search_path(self):
        env = {"LOBSTER_KERNEL": str(self.tmp / "none")}
        with patch.dict(os.environ, env), patch.object(paths, "REPO_ROOT", self.tmp):
            message = firstrun.missing_components_message()
        self.assertIn("kernel8.img not found", message)
        self.assertIn(str(self.tmp / "none"), message)

    def test_no_message_when_complete(self):
        with patch.object(paths, "missing_components", return_value=[]):
            self.assertEqual(firstrun.missing_components_message(), "")


class TestScreenshotWrite(TempDirCase):
    @staticmethod
    def wait_written(path):
        Machine._wait_file_written(None, path)  # needs no VM state

    def test_empty_file_is_not_accepted(self):
        target = self.tmp / "s.png"
        target.write_bytes(b"")
        with patch("lobstervm.machine.SCREENSHOT_WRITE_TIMEOUT_S", 0.5), \
                self.assertRaises(VmError):
            self.wait_written(target)

    def test_stable_non_empty_file_is_accepted(self):
        target = self.tmp / "s.png"
        target.write_bytes(b"png")
        self.wait_written(target)


class TestPpmToPng(unittest.TestCase):
    def test_roundtrip_through_pillow(self):
        from io import BytesIO

        from PIL import Image

        from lobstervm import ppm
        pixels = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 10, 20, 30])
        png = ppm.ppm_to_png(b"P6\n2 2\n255\n" + pixels)
        image = Image.open(BytesIO(png)).convert("RGB")
        self.assertEqual(image.size, (2, 2))
        self.assertEqual(list(image.getdata()), [(255, 0, 0), (0, 255, 0), (0, 0, 255),
                                                 (10, 20, 30)])

    def test_rejects_truncated_pixels_and_wrong_magic(self):
        from lobstervm import ppm
        with self.assertRaises(ValueError):
            ppm.ppm_to_png(b"P6\n2 2\n255\nabc")
        with self.assertRaises(ValueError):
            ppm.ppm_to_png(b"P5\n1 1\n255\nx")


class TestScreenshotFallback(TempDirCase):
    def test_png_falls_back_to_ppm_when_qemu_lacks_libpng(self):
        from PIL import Image
        machine = Machine.__new__(Machine)
        target = self.tmp / "s.png"

        def fake_hmp(command, **_):
            if "-f png" in command:
                return "Error: Enable PNG support with libpng for screendump"
            Path(command.split('"')[1]).write_bytes(b"P6\n1 1\n255\n\x01\x02\x03")
            return ""
        machine.hmp = fake_hmp
        self.assertEqual(machine.screenshot(target), target)
        self.assertEqual(Image.open(target).convert("RGB").getpixel((0, 0)), (1, 2, 3))
        self.assertFalse(target.with_suffix(".ppm").exists())


class TestQuickstartCommand(TempDirCase):
    def run_quickstart(self, store, problem=""):
        from lobstervm import cli
        base = self.tmp / "base.img"
        base.write_bytes(b"")
        with patch.object(firstrun, "missing_components_message", return_value=problem), \
                patch("lobstervm.config.run_qemu_img"), \
                patch.dict(os.environ, {"LOBSTER_BASE_DISK": str(base)}):
            return cli.cmd_quickstart(store, None)

    def test_creates_default_vm_once(self):
        store = VmStore(self.tmp / "vms")
        self.assertEqual(self.run_quickstart(store), 0)
        self.assertEqual(store.list_names(), ["LobsterOS"])
        self.assertEqual(self.run_quickstart(store), 0)
        self.assertEqual(store.list_names(), ["LobsterOS"])

    def test_missing_components_fail_without_creating(self):
        store = VmStore(self.tmp / "vms")
        self.assertEqual(self.run_quickstart(store, "kernel8.img not found"), 1)
        self.assertEqual(store.list_names(), [])


if __name__ == "__main__":
    unittest.main()
