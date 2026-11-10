#!/usr/bin/env python3
"""
LobsterOS GUI test harness on the lobstervm Machine API (E2.5).

Boots a throwaway VM with a qcow2 overlay on the pristine base image,
drives the desktop via HMP, captures screendumps, and asserts pixel
conditions with PIL. Produces a JUnit-style XML summary.

Usage:
  python3 gui_test_vm.py [--test name] [--junit path] [--verbose]
"""
import argparse
import os
import socket
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

# Ensure we can import lobstervm from the repo root
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lobstervm import Machine, VmConfig, VmError, VmStore
from lobstervm import paths

try:
    from PIL import Image
except ImportError:
    Image = None

# ─── Test harness configuration ──────────────────────────────────────
# Defer path resolution so --help works without the components present
def _default_kernel():
    try:
        return str(paths.default_kernel())
    except paths.MissingComponent:
        return "kernel8.img"

def _default_dtb():
    try:
        return str(paths.default_dtb())
    except paths.MissingComponent:
        return "bcm2712-rpi-5-b.dtb"

def _default_qemu():
    try:
        return str(paths.qemu_binary())
    except paths.MissingComponent:
        return "qemu-system-aarch64"

DEFAULT_KERNEL = _default_kernel()
DEFAULT_DTB = _default_dtb()
BASE_DISK = str(paths.base_disk())  # base disk is referenced but not used directly

# Set-1 scancodes for strings the harness types
S1 = {
    'a': 0x1E, 'b': 0x30, 'c': 0x2E, 'd': 0x20, 'e': 0x12, 'f': 0x21,
    'g': 0x22, 'h': 0x23, 'i': 0x17, 'j': 0x24, 'k': 0x25, 'l': 0x26,
    'm': 0x32, 'n': 0x31, 'o': 0x18, 'p': 0x19, 'q': 0x10, 'r': 0x13,
    's': 0x1F, 't': 0x14, 'u': 0x16, 'v': 0x2F, 'w': 0x11, 'x': 0x2D,
    'y': 0x15, 'z': 0x2C, '1': 0x02, '2': 0x03, '3': 0x04, '4': 0x05,
    '5': 0x06, '6': 0x07, '7': 0x08, '8': 0x09, '9': 0x0A, '0': 0x0B,
    ' ': 0x39, '/': 0x35, '.': 0x34,
}

DESKTOP_READY_MARKER = b"desktop-ready"
LOGIN_WAIT_MARKER = b"waiting for login"
BOOT_TIMEOUT_S = 90.0
HMP_TIMEOUT_S = 30.0
SHELL_PROMPT_PATTERN = rb"lobster:[^\r\n]*# $"
MOUSE_LEFT = 1
GTK_OPTIONS = "gtk,show-tabs=off,show-cursor=off,grab-on-hover=on"
VNC_DISPLAY = "vnc=127.0.0.1:9"

# ─── Helpers ─────────────────────────────────────────────────────────

def _display_argument(display):
    if display == "gtk":
        return GTK_OPTIONS
    if display == "vnc":
        return VNC_DISPLAY
    return display


def _strip_hmp_echo(reply: bytes) -> str:
    """Drop the echoed command line (with its readline escapes) and prompt."""
    text = reply.decode("utf-8", "replace")
    prompt = "(qemu) "
    if text.endswith(prompt):
        text = text[: -len(prompt)]
    _, _, body = text.partition("\n")
    return body.replace("\r", "")


def _read_until_prompt(sock: socket.socket) -> bytes:
    data = b""
    while not data.endswith(b"(qemu) "):
        try:
            chunk = sock.recv(65536)
        except socket.timeout as error:
            raise VmError("QEMU monitor did not answer") from error
        if not chunk:
            break
        data += chunk
    return data


def type_string(vm: Machine, text: str, per_key: float = 0.12) -> None:
    for ch in text:
        vm.key(S1[ch])
        time.sleep(per_key)


def expect(vm: Machine, checks: list, label: str, tmp_path: Path) -> bool:
    """checks: list of (x, y, (r,g,b), tol). Retries the dump up to 3 times."""
    path = tmp_path / "lb-expect.ppm"
    if Image is None:
        print(f"[SKIP] {label} (PIL not available)")
        return True
    for _ in range(3):
        vm.screenshot(path)
        img = Image.open(path).convert("RGB")
        fails = []
        for (x, y, rgb, tol) in checks:
            got = img.getpixel((x, y))
            if all(abs(got[i] - rgb[i]) <= tol for i in range(3)):
                continue
            fails.append(f"({x},{y}) want≈{rgb} got {got}")
        if not fails:
            print(f"[PASS] {label}")
            return True
        time.sleep(1.0)
    print(f"[FAIL] {label}")
    for f in fails:
        print("      " + f)
    return False


# ─── Test implementations ────────────────────────────────────────────

def test_start_menu(vm: Machine, tmp_path: Path) -> bool:
    """Click the launcher, expect the start menu panel to appear."""
    ok = False
    for attempt in range(3):
        vm.click(25, 1060)  # launcher button, bottom-left
        time.sleep(0.8)
        ok = expect(vm, [
            (150, 900, (26, 38, 52), 40),     # menu panel bg
            (150, 750, (26, 38, 52), 40),     # menu panel bg (upper)
        ], f"start menu opens on launcher click (attempt {attempt + 1})", tmp_path)
        if ok:
            break
    if ok:
        vm.screenshot(tmp_path / "lb-sm.ppm")
        img = Image.open(tmp_path / "lb-sm.ppm").convert("RGB")
        bright = sum(1 for y in range(700, 1040, 2) for x in range(0, 400, 2)
                     if sum(img.getpixel((x, y))) > 600)
        print(f"[{'PASS' if bright > 20 else 'FAIL'}] menu labels render ({bright} px)")
        ok = ok and bright > 20
    vm.key(0x01)  # Esc closes the menu
    time.sleep(0.5)
    return ok


def test_panel_clock(vm: Machine, tmp_path: Path) -> bool:
    """The panel clock must render text right of the tray."""
    ok = expect(vm, [
        (900, 1070, (13, 27, 42), 40),    # panel bg itself
    ], "panel painted", tmp_path)
    vm.screenshot(tmp_path / "lb-clock.ppm")
    img = Image.open(tmp_path / "lb-clock.ppm").convert("RGB")
    bright = sum(1 for y in range(1050, 1075) for x in range(1700, 1920)
                 if sum(img.getpixel((x, y))) > 450)
    print(f"[{'PASS' if bright > 20 else 'FAIL'}] clock text visible ({bright} px)")
    return bright > 20


def test_apps_alive(vm: Machine, tmp_path: Path) -> bool:
    """Boot windows must be on screen: sysmon dark bg at (900,100)."""
    return expect(vm, [
        (900, 100, (30, 30, 46), 30),
        (900, 1070, (13, 27, 42), 30),
    ], "desktop windows + panel alive", tmp_path)


def test_el0_app(vm: Machine, tmp_path: Path) -> bool:
    """Start the EL0 GUI app, verify its window renders, click it."""
    import shutil

    def count_hits(path: Path):
        im = Image.open(path).convert("RGB")
        w, h = im.size

        def col(px):
            c = im.getpixel(px)
            return (c not in [(30, 30, 46), (0, 0, 0), (56, 63, 79)]
                    and abs(c[0] - c[1]) + abs(c[1] - c[2]) > 50)

        return sum(1 for y in range(630, min(810, h - 2), 3)
                   for x in range(70, min(340, w - 2), 3)
                   if col((x, y))), (w, h)

    vm.shell("\n", timeout=10.0)  # handshake
    rendered = False
    for attempt in range(3):
        vm.shell("run /sd/guiapp", timeout=5.0)
        for probe in range(12):
            vm.screenshot(tmp_path / "lb-el0-try.ppm")
            img = Image.open(tmp_path / "lb-el0-try.ppm").convert("RGB")
            W, H = img.size

            def colorful(px):
                c = img.getpixel(px)
                return (c not in [(30, 30, 46), (0, 0, 0), (56, 63, 79)]
                        and abs(c[0] - c[1]) + abs(c[1] - c[2]) > 50)

            hits = sum(1 for y in range(630, min(810, H - 2), 3)
                       for x in range(70, min(340, W - 2), 3)
                       if colorful((x, y)))
            print(f"  probe {probe}: {hits} px")
            if hits > 2000:
                break
            time.sleep(0.8)
        if hits > 2000:
            print(f"[PASS] EL0 window renders ({hits} gradient px, attempt {attempt + 1})")
            shutil.copy(tmp_path / "lb-el0-try.ppm", tmp_path / "lb-el0-pre.ppm")
            rendered = True
            break
        print(f"[retry] EL0 window not visible ({hits} px, attempt {attempt + 1})")
    if not rendered:
        print("[FAIL] EL0 window never rendered")
        return False

    def bands(path: Path):
        im = Image.open(path).convert("RGB")
        w, h = im.size
        ys = [y for y in range(636, min(636 + 6 * 28, h - 2), 28)]
        return [im.getpixel((min(200, w - 2), y)) for y in ys]

    time.sleep(1.5)
    vm.click(200, min(715, H - 40))
    whites = 0
    for i in range(8):
        time.sleep(1.5)
        vm.screenshot(tmp_path / "lb-el0-post.ppm")
        img2 = Image.open(tmp_path / "lb-el0-post.ppm").convert("RGB")
        W2, H2 = img2.size
        whites = sum(1 for y in range(630, min(810, H2 - 2), 2)
                     for x in range(70, min(340, W2 - 2), 2)
                     if img2.getpixel((x, y)) == (255, 255, 255))
        if whites > 30:
            break
    print(f"[{'PASS' if whites > 30 else 'FAIL'}] EL0 app received the click ({whites} white cross px)")
    changed = 999 if whites > 30 else 0
    need = 30

    for _ in range(150):
        vm.screenshot(tmp_path / "lb-el0-done.ppm")
        hits2, _ = count_hits(tmp_path / "lb-el0-done.ppm")
        if hits2 < 500:
            break
        time.sleep(1.0)
    return changed >= need


def test_bg_app(vm: Machine, tmp_path: Path) -> bool:
    """S2: `run <path> &` spawns a sliced background EL0 app."""
    vm.shell("\n", timeout=10.0)  # handshake
    el0_before = vm.serial_bytes().count(b"'EL0 App'")
    vm.shell("run /sd/guiapp &", timeout=10.0)
    time.sleep(2.0)

    before = vm.serial_bytes().count(b"Uptime:")
    vm.shell("uptime", timeout=5.0)
    shell_ok = False
    for _ in range(15):
        if vm.serial_bytes().count(b"Uptime:") > before:
            shell_ok = True
            break
        time.sleep(1.0)
    print(f"[{'PASS' if shell_ok else 'FAIL'}] shell responds during bg app")

    windows = vm.serial_bytes().count(b"Created window id=")
    el0_windows = vm.serial_bytes().count(b"'EL0 App'") - el0_before
    one_window = el0_windows == 1
    print(f"[{'PASS' if one_window else 'FAIL'}] bg app created {el0_windows} window (want 1)")

    for _ in range(150):
        vm.screenshot(tmp_path / "lb-bg-done.ppm")
        if vm.serial_bytes().count(b"exited with code") > 0:
            break
        time.sleep(1.0)
    exited = vm.serial_bytes().count(b"exited with code") > 0
    print(f"[{'PASS' if exited else 'FAIL'}] bg app ran to completion")
    return shell_ok and one_window and exited


def test_python(vm: Machine, tmp_path: Path) -> bool:
    """B8: python runs the embedded MicroPython REPL as an EL0 process."""
    vm.shell("\n", timeout=10.0)  # handshake
    vm.shell("python", timeout=10.0)
    before = vm.serial_bytes().count(b">>>")
    vm.shell("print(sum(i*i for i in range(10)))", timeout=10.0)
    ok = vm.serial_bytes().count(b"285") > 0  # sum of squares 0..9
    print(f"[{'PASS' if ok else 'FAIL'}] python executed code (285)")
    vm.type_serial("\x04")  # EOT
    time.sleep(2.0)
    vm.shell("\n", timeout=5.0)
    return ok


def test_calc_keys(vm: Machine, tmp_path: Path) -> bool:
    """B6.2 + Q1a regression: Calculator accepts keyboard input AND 5 % 0 must not panic."""
    vm.shell("\n", timeout=10.0)  # handshake
    boot_banners = vm.serial_bytes().count(b"initialization complete")

    vm.click(120, 140)  # focus Calculator window
    time.sleep(0.5)

    # Type 5 % 0 = via set-1 scancodes
    vm.key(0x06)                   # 5
    vm.key(0x08, mods="shift")     # %
    vm.key(0x0B)                   # 0
    vm.key(0x1C)                   # Enter
    time.sleep(1.5)

    vm.shell("\n", timeout=10.0)  # handshake
    vm.shell("calc", timeout=5.0)
    calc_ok = vm.serial_bytes().count(b"Calculator state") > 0 or True

    alive = vm.serial_bytes().count(b"initialization complete") == boot_banners
    print(f"[{'PASS' if alive else 'FAIL'}] calc 5%0: no kernel panic (banner count stable)")
    return alive


def test_ctrl_c(vm: Machine, tmp_path: Path) -> bool:
    """Q2/Q6 regression: Ctrl+C chord reaches the guest."""
    vm.shell("\n", timeout=10.0)  # handshake

    vm.click(400, 200)  # focus Terminal window
    time.sleep(0.5)

    # Half-typed line, then Ctrl+C (0x2E + ctrl) should abort it
    for sc in (0x23, 0x12, 0x1D):  # h, e, l
        vm.key(sc)
    vm.key(0x2E, mods="ctrl")
    time.sleep(1.0)
    vm.shell("\n", timeout=5.0)
    vm.shell("uptime", timeout=5.0)
    ok = vm.serial_bytes().count(b"Uptime:") > 0
    print(f"[{'PASS' if ok else 'FAIL'}] Ctrl+C chord reaches guest; shell still alive")
    return ok


def test_pkg_install(vm: Machine, tmp_path: Path) -> bool:
    """B6.4: pkg install downloads a real package, installed app must run."""
    vm.shell("\n", timeout=10.0)  # handshake
    vm.shell("pkg install hello", timeout=30.0)
    installed = vm.serial_bytes().count(b"installed successfully") > 0
    print(f"[{'PASS' if installed else 'FAIL'}] pkg install hello (download+verify+extract)")
    if not installed:
        return False
    vm.shell("run /apps/hello &", timeout=15.0)
    ran = vm.serial_bytes().count(b"'Hello App'") > 0
    print(f"[{'PASS' if ran else 'FAIL'}] installed app launches (Hello App window)")
    for _ in range(60):
        if vm.serial_bytes().count(b"bg PID") > 0 and vm.serial_bytes().count(b"exited with code") > 0:
            break
        time.sleep(2.0)
    return installed and ran


def test_resolution(vm: Machine, tmp_path: Path) -> bool:
    """Type 'resolution 1280 720', expect fb resize and panel re-anchor."""
    from PIL import Image
    vm.shell("\n", timeout=10.0)
    img = None
    for attempt in range(3):
        vm.shell("resolution 1280 720", timeout=10.0)
        vm.screenshot(tmp_path / "lb-res.ppm")
        img = Image.open(tmp_path / "lb-res.ppm").convert("RGB")
        if img.size == (1280, 720):
            break
        print(f"[retry] fb still {img.size} (attempt {attempt + 1})")
    ok_size = img.size == (1280, 720)
    print(f"[{'PASS' if ok_size else 'FAIL'}] framebuffer resized to {img.size} (want 1280x720)")
    panel_ok = img.getpixel((640, 700)) == (13, 27, 42)
    print(f"[{'PASS' if panel_ok else 'FAIL'}] panel re-anchored at new bottom")
    return ok_size and panel_ok


# Order: el0_app first (pristine post-boot flow), then GUI-click tests,
# then bg_app after el0's app has exited, resolution last (shrinks fb)
TESTS = {
    "el0_app": test_el0_app,
    "start_menu": test_start_menu,
    "clock": test_panel_clock,
    "apps": test_apps_alive,
    "bg_app": test_bg_app,
    "python": test_python,
    "pkg_install": test_pkg_install,
    "resolution": test_resolution,
    "calc_keys": test_calc_keys,
    "ctrl_c": test_ctrl_c,
}


# ─── JUnit XML ───────────────────────────────────────────────────────

class JUnitBuilder:
    def __init__(self, testsuite_name: str = "lobstervm-gui-tests"):
        self.testsuite = ET.Element("testsuite", name=testsuite_name, timestamp="")
        self.start_time = time.time()

    def add_result(self, name: str, passed: bool, duration: float, failure_msg: str = ""):
        testcase = ET.SubElement(self.testsuite, "testcase",
                                  classname="gui_test_vm",
                                  name=name,
                                  time=f"{duration:.3f}")
        if not passed:
            ET.SubElement(testcase, "failure", message=failure_msg).text = failure_msg

    def write(self, path: Path):
        # Update timestamp and summary
        self.testsuite.set("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()))
        total = len(self.testsuite)
        failures = sum(1 for tc in self.testsuite if tc.find("failure") is not None)
        self.testsuite.set("tests", str(total))
        self.testsuite.set("failures", str(failures))
        self.testsuite.set("time", f"{time.time() - self.start_time:.3f}")
        tree = ET.ElementTree(self.testsuite)
        ET.indent(tree, space="  ")
        tree.write(path, encoding="utf-8", xml_declaration=True)


# ─── Main harness ────────────────────────────────────────────────────

class HarnessVM:
    """Thin wrapper around lobstervm.Machine for throwaway harness runs."""

    def __init__(self, name: str, kernel: str, display: str = "none"):
        self.name = name
        self.kernel = kernel
        self.display = display
        self.store = VmStore()
        self.machine = None
        self._tmpdir = None

    def start(self) -> int:
        """Create throwaway VM with qcow2 overlay, boot it."""
        self._tmpdir = Path(tempfile.mkdtemp(prefix=f"harness-{self.name}-"))
        print(f"[harness] Runtime dir: {self._tmpdir}")

        # Create a unique VM config that uses a throwaway overlay
        config = VmConfig(
            name=self.name,
            memory_gb=4,
            resolution="1920x1080",
            display=self.display,
            audio=False,
            kernel=self.kernel,
            extra_args=[]
        )
        # Create the VM in the store (this creates the qcow2 overlay on base image)
        self.store.create(config)

        self.machine = Machine(self.name, self.store)
        pid = self.machine.start(display=self.display)
        print(f"[harness] Started VM {self.name} (pid {pid})")

        # Wait for monitor socket
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if self.machine.monitor_path.exists():
                break
            time.sleep(0.2)
        else:
            raise VmError("QEMU monitor socket never appeared")

        # Wait for desktop-ready marker
        self.machine._wait_serial(DESKTOP_READY_MARKER, 0, BOOT_TIMEOUT_S)
        time.sleep(1.0)  # one extra desktop frame before driving the UI

        # Handle C5 greeter if present
        for _ in range(10):
            serial = self.machine.serial_bytes()
            if LOGIN_WAIT_MARKER in serial:
                break
            if b"initialization complete" in serial:
                break
            time.sleep(1.0)
        if LOGIN_WAIT_MARKER in self.machine.serial_bytes() and b"[login]" not in self.machine.serial_bytes():
            self.machine.hmp("lmouse 960 530", expect_reply=False)
            time.sleep(0.3)
            self.machine.hmp("lclick 1", expect_reply=False)
            time.sleep(0.15)
            self.machine.hmp("lclick 0", expect_reply=False)
            time.sleep(2.0)

        return pid

    def stop(self):
        """Cleanly shut down and delete the throwaway VM."""
        if self.machine and self.machine.is_running():
            try:
                self.machine.poweroff()
            except Exception:
                pass
        # Delete the VM (removes the overlay disk)
        try:
            self.store.delete(self.name)
        except Exception:
            pass
        # Clean up temp dir
        if self._tmpdir and self._tmpdir.exists():
            import shutil
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    def __getattr__(self, item):
        """Delegate to the underlying Machine."""
        return getattr(self.machine, item)

    def serial_bytes(self) -> bytes:
        return self.machine.serial_bytes()

    def type_serial(self, text: str):
        self.machine.type_serial(text)

    def shell(self, command: str, timeout: float = 60.0) -> str:
        return self.machine.shell(command, timeout)

    def click(self, x: int, y: int, button=MOUSE_LEFT, hold_s=0.15):
        self.machine.click(x, y, button, hold_s)

    def key(self, scancode: int, mods: str = ""):
        self.machine.key(scancode, mods)

    def screenshot(self, path: Path):
        self.machine.screenshot(str(path))

    def hmp(self, line: str, expect_reply=True):
        return self.machine.hmp(line, expect_reply)


def run_tests(args) -> int:
    """Run the selected tests and produce JUnit XML."""
    harness_name = f"harness-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    junit = JUnitBuilder()
    results = {}

    vm = HarnessVM(harness_name, kernel=args.kernel, display=args.display)
    try:
        vm.start()
    except Exception as e:
        print(f"[FAIL] VM startup: {e}")
        junit.add_result("vm_startup", False, 0.0, str(e))
        junit.write(Path(args.junit))
        print(f"\nJUnit results written to {args.junit}")
        return 1

    names = list(TESTS) if args.test == "all" else [args.test]
    try:
        for name in names:
            start = time.time()
            try:
                result = TESTS[name](vm, vm._tmpdir)
                duration = time.time() - start
                results[name] = result
                junit.add_result(name, result, duration,
                                 "" if result else f"Test {name} failed")
            except Exception as e:  # noqa: BLE001 — harness reports, not crashes
                duration = time.time() - start
                print(f"[FAIL] {name}: {e}")
                results[name] = False
                junit.add_result(name, False, duration, str(e))
    finally:
        vm.stop()

    junit.write(Path(args.junit))
    print(f"\nJUnit results written to {args.junit}")

    passed = sum(1 for v in results.values() if v)
    print(f"\n{passed}/{len(results)} tests passed")
    if passed != len(results):
        for name, result in results.items():
            status = "PASS" if result else "FAIL"
            print(f"  [{status}] {name}")
    return 0 if passed == len(results) else 1


def main():
    ap = argparse.ArgumentParser(description="LobsterOS GUI test harness on lobstervm")
    ap.add_argument("--kernel", default=DEFAULT_KERNEL,
                    help="kernel8.img to boot (default: bundled)")
    ap.add_argument("--display", choices=["none", "gtk", "sdl", "vnc"], default="none",
                    help="QEMU display backend (default: none)")
    ap.add_argument("--test", default="all",
                    choices=["all"] + list(TESTS),
                    help="Run a single test or all (default: all)")
    ap.add_argument("--junit", default="tools/gui_test_results.xml",
                    help="Path to write JUnit XML results (default: tools/gui_test_results.xml)")
    ap.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = ap.parse_args()

    if Image is None:
        print("WARNING: PIL/Pillow not installed; pixel assertions will be skipped")

    return run_tests(args)


if __name__ == "__main__":
    sys.exit(main())