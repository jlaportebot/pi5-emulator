#!/usr/bin/env python3
"""
LobsterOS GUI test harness on the lobstervm Machine API (E2.5).

Boots a throwaway VM with a qcow2 overlay on the pristine base image,
drives the desktop via HMP, captures screendumps, and asserts pixel
conditions with PIL. Produces a JUnit-style XML summary.

This is the one GUI harness (tools/gui_test.py only forwards here).
Commands that return to the shell prompt go through Machine.shell();
foreground GUI apps and REPL input are TYPED without waiting
(Machine.send_line) and the expected evidence is polled for afterwards.

The desktop boots clean: no app windows, only a one-time Welcome window.
Apps are launched with the guest `app <name>` command.

Usage:
  python3 gui_test_vm.py [--test name] [--junit path] [--output-dir dir]
"""
import argparse
import re
import shutil
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

PILLOW_MISSING_MESSAGE = "error: Pillow is required for pixel assertions (pip install pillow)"


def _default_kernel():
    """Resolve lazily so --help works without the components present."""
    try:
        return str(paths.default_kernel())
    except paths.MissingComponent:
        return "kernel8.img"


DEFAULT_KERNEL = _default_kernel()

DESKTOP_READY_MARKER = b"desktop-ready"
LOGIN_WAIT_MARKER = b"waiting for login"
UPTIME_MARKER = b"Uptime:"
EXITED_MARKER = b"exited with code"
CTRL_C = "\x03"
CTRL_D = "\x04"
BOOT_TIMEOUT_S = 90.0
MONITOR_TIMEOUT_S = 20.0
MOUSE_LEFT = 1

# Shell handshake: the cooperative EL0 demo can keep the shell busy for ~2
# minutes under TCG, so the handshake must tolerate that.
SHELL_READY_TIMEOUT_S = 150.0
PKG_INSTALL_TIMEOUT_S = 180.0
SHELL_NUDGE_AFTER_S = 20.0
HANDSHAKE_POLL_S = 1.0

# Input pacing, mirroring the legacy harness (mouse move settle, press
# duration, post-release settle, post-key settle).
MOVE_SETTLE_S = 0.2
CLICK_HOLD_S = 0.15
CLICK_SETTLE_S = 0.4
KEY_SETTLE_S = 0.25

# Fixed screen geometry and colours of the 1920x1080 desktop.
LAUNCHER_XY = (25, 1060)
PANEL_RGB = (13, 27, 42)
MENU_RGB = (26, 38, 52)
WINDOW_RGB = (30, 30, 46)
EL0_IGNORED_RGB = [(30, 30, 46), (0, 0, 0), (56, 63, 79)]
EL0_REGION = (70, 340, 630, 810)  # x0, x1, y0, y1
EL0_RENDER_MIN_PX = 2000
EL0_GONE_MAX_PX = 500
EL0_CLICK_MIN_WHITE_PX = 30
EL0_CLICK_XY = (200, 715)
RESIZED_SIZE = (1280, 720)
RESIZED_PANEL_XY = (640, 700)
RESIZE_SETTLE_TIMEOUT_S = 20.0

# Clean-boot desktop (1920x1080). Every XY is a screen pixel.
WELCOME_TITLE = "Welcome to LobsterOS"
WELCOME_FIRST_BOOT_MARKER = b"[welcome] First boot detected"
WELCOME_SKIPPED_MARKER = b"[welcome] Welcome already seen, skipping"
WELCOME_CLOSE_XY = (1197, 377)    # red close button of the centred Welcome window
WELCOME_CLOSE_POLLS = 10
WINS_TIMEOUT_S = 30.0
FILES_BODY_XY = (550, 300)        # white file list of the File Manager
FILES_BODY_RGB = (246, 248, 250)
CALC_TITLE_XY = (260, 154)        # Calculator title bar (focus target)
TERMINAL_TITLE_XY = (400, 94)     # Terminal title bar (focus target)
TERMINAL_BODY = (104, 816, 128, 576)  # x0, x1, y0, y1 of the Terminal text area
# x-height of the 13 px mono face is ~7 rows; descender and accent fragments of
# anti-aliased text are 1-3 rows and must not count as a text line.
MIN_TEXT_BAND_ROWS = 5
TEXT_RGB_SUM_MIN = 450            # brightness sum separating glyphs from background
TERMINAL_BANNER_MAX_LINES = 4     # welcome banner is 3 text lines; a log leak is far more
APP_LAUNCHED_MARKER = "app: launched"
WINDOW_TITLE_RE = re.compile(rb"\[wm\] Created window id=\d+ '([^']*)'")
FILES_TITLE = "File Manager"
CALC_TITLE = "Calculator"
TERMINAL_TITLE = "Terminal"
APP_LAUNCH_TIMEOUT_S = 30.0
KEY_SC = {"h": 0x23, "e": 0x12, "l": 0x26, "5": 0x06, "0": 0x0B, "enter": 0x1C,
          "esc": 0x01, "c": 0x2E}
CALC_STATE_PREFIX = "Calculator state: display="
CALC_ERROR_DISPLAY = "Error"
# Shift+5 types '%'. The guest's HID path reads the QEMU flag bits with a
# different layout (QEMU bit1=ctrl is HID LShift), so "ctrl" is what
# produces Shift in the guest on current kernels; plain "shift" types a '5'.
PERCENT_MODS = "ctrl"
MENU_PROBE_POINTS = ((150, 900), (150, 750))
MENU_TOLERANCE = 20  # tighter than the open-check: the desktop behind is within 30 of the menu colour
MENU_SETTLE_S = 0.8


# ─── Helpers ─────────────────────────────────────────────────────────

def serial_count(vm, marker: bytes) -> int:
    return vm.serial_bytes().count(marker)


def wait_count_above(vm, marker: bytes, baseline: int, timeout: float) -> bool:
    """Poll until ``marker`` occurs more than ``baseline`` times on serial."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if serial_count(vm, marker) > baseline:
            return True
        time.sleep(0.5)
    return serial_count(vm, marker) > baseline


def ensure_shell(vm, timeout: float = SHELL_READY_TIMEOUT_S) -> None:
    """Bring the guest back to an idle shell (legacy ``wait_ready`` handshake).

    Types ``uptime`` until the guest answers, which proves the shell loop is
    polling serial again. If it stays silent for a while, Ctrl+C is sent once
    to leave a REPL or abort a half-typed line. Input typed while the shell
    is blocked can be lost to the UART FIFO, hence the repeats.
    """
    start = time.monotonic()
    before = serial_count(vm, UPTIME_MARKER)
    nudged = False
    while time.monotonic() - start < timeout:
        vm.send_line("uptime")
        if wait_count_above(vm, UPTIME_MARKER, before, HANDSHAKE_POLL_S):
            return
        if not nudged and time.monotonic() - start > SHELL_NUDGE_AFTER_S:
            print("[harness] guest shell silent, sending Ctrl+C")
            vm.type_serial(CTRL_C)
            nudged = True
    raise VmError(f"guest shell did not answer 'uptime' within {timeout:.0f}s")


def parse_window_titles(serial: bytes) -> list:
    """Titles of the windows the window manager logged on serial, in order."""
    return [t.decode("utf-8", "replace") for t in WINDOW_TITLE_RE.findall(serial)]


def app_window_titles(titles: list) -> list:
    """Titles that are real applications (the first-run Welcome is not one)."""
    return [t for t in titles if t != WELCOME_TITLE]


def count_text_lines(img, box, threshold: int = TEXT_RGB_SUM_MIN) -> int:
    """Number of text lines (bands of bright rows at least MIN_TEXT_BAND_ROWS tall) in box."""
    x0, x1, y0, y1 = box
    bands, run = 0, 0
    for y in range(y0, y1):
        has_text = any(sum(img.getpixel((x, y))) > threshold for x in range(x0, x1, 2))
        run = run + 1 if has_text else 0
        if run == MIN_TEXT_BAND_ROWS:
            bands += 1
    return bands


def load_image(vm, path: Path):
    vm.screenshot(path)
    return Image.open(path).convert("RGB")


def expect(vm, checks: list, label: str, tmp_path: Path) -> bool:
    """checks: list of (x, y, (r,g,b), tol). Retries the dump up to 3 times."""
    fails = []
    for _ in range(3):
        img = load_image(vm, tmp_path / "lb-expect.ppm")
        fails = []
        for (x, y, rgb, tol) in checks:
            got = img.getpixel((x, y))
            if not all(abs(got[i] - rgb[i]) <= tol for i in range(3)):
                fails.append(f"({x},{y}) want≈{rgb} got {got}")
        if not fails:
            print(f"[PASS] {label}")
            return True
        time.sleep(1.0)
    print(f"[FAIL] {label}")
    for f in fails:
        print("      " + f)
    return False


def report(ok: bool, message: str) -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {message}")
    return ok


def count_bright(img, box, threshold: int, step: int = 2) -> int:
    x0, x1, y0, y1 = box
    return sum(1 for y in range(y0, y1, step) for x in range(x0, x1, step)
               if sum(img.getpixel((x, y))) > threshold)


def count_gradient_px(img) -> int:
    """Colourful (non-grey, non-background) pixels in the EL0 window area."""
    x0, x1, y0, y1 = EL0_REGION
    width, height = img.size

    def colorful(px):
        c = img.getpixel(px)
        return c not in EL0_IGNORED_RGB and abs(c[0] - c[1]) + abs(c[1] - c[2]) > 50

    return sum(1 for y in range(y0, min(y1, height - 2), 3)
               for x in range(x0, min(x1, width - 2), 3)
               if colorful((x, y)))


def count_white_px(img) -> int:
    x0, x1, y0, y1 = EL0_REGION
    width, height = img.size
    return sum(1 for y in range(y0, min(y1, height - 2), 2)
               for x in range(x0, min(x1, width - 2), 2)
               if img.getpixel((x, y)) == (255, 255, 255))


# ─── Test implementations ────────────────────────────────────────────

def test_start_menu(vm, tmp_path: Path) -> bool:
    """Click the launcher, expect the start menu panel to appear."""
    ok = False
    for attempt in range(3):
        vm.click(*LAUNCHER_XY)
        time.sleep(0.8)
        ok = expect(vm, [
            (150, 900, MENU_RGB, 40),     # menu panel bg
            (150, 750, MENU_RGB, 40),     # menu panel bg (upper)
        ], f"start menu opens on launcher click (attempt {attempt + 1})", tmp_path)
        if ok:
            break
    if ok:
        img = load_image(vm, tmp_path / "lb-sm.ppm")
        bright = count_bright(img, (0, 400, 700, 1040), 600)
        ok = report(bright > 20, f"menu labels render ({bright} px)")
    return close_start_menu(vm, tmp_path) and ok


def menu_is_open(vm, tmp_path: Path) -> bool:
    """True when every start-menu probe pixel shows the menu background."""
    img = load_image(vm, tmp_path / "lb-menu.ppm")
    return all(
        all(abs(img.getpixel(point)[i] - MENU_RGB[i]) <= MENU_TOLERANCE for i in range(3))
        for point in MENU_PROBE_POINTS)


def close_start_menu(vm, tmp_path: Path) -> bool:
    """Close the menu like a user: click the start button again."""
    if not menu_is_open(vm, tmp_path):
        return report(True, "start menu already closed")
    vm.click(*LAUNCHER_XY)
    time.sleep(MENU_SETTLE_S)
    return report(not menu_is_open(vm, tmp_path), "start menu closes on second launcher click")


def test_start_menu_esc(vm, tmp_path: Path) -> bool:
    """Esc should close the start menu. Known to fail on kernels without the fix."""
    vm.click(*LAUNCHER_XY)
    time.sleep(MENU_SETTLE_S)
    if not menu_is_open(vm, tmp_path):
        return report(False, "start menu did not open for the Esc check")
    vm.key(KEY_SC["esc"])
    time.sleep(MENU_SETTLE_S)
    closed = not menu_is_open(vm, tmp_path)
    if closed:
        print("[XPASS] !!! Esc now closes the start menu: turn this into a real assertion !!!")
        return True
    print("[XFAIL] Esc does not close the start menu (expected on kernels without the fix)")
    return close_start_menu(vm, tmp_path)


def test_panel_clock(vm, tmp_path: Path) -> bool:
    """The panel clock must render text right of the tray."""
    expect(vm, [(900, 1070, PANEL_RGB, 40)], "panel painted", tmp_path)
    img = load_image(vm, tmp_path / "lb-clock.ppm")
    bright = count_bright(img, (1700, 1920, 1050, 1075), 450, step=1)
    return report(bright > 20, f"clock text visible ({bright} px)")


def launch_app(vm, name: str, title: str) -> bool:
    """Run `app <name>`; true if the guest says launched and logs the window."""
    offset = len(vm.serial_bytes())
    vm.shell(f"app {name}", timeout=APP_LAUNCH_TIMEOUT_S)
    time.sleep(1.0)  # let the compositor paint the new window
    serial = vm.serial_bytes()[offset:]
    launched = report(APP_LAUNCHED_MARKER.encode() in serial, f"app {name}: guest reports launched")
    created = report(title in parse_window_titles(serial), f"app {name}: '{title}' window created")
    return launched and created


def test_apps_alive(vm, tmp_path: Path) -> bool:
    """Clean boot: no app windows at boot, panel alive, then `app files` opens one."""
    boot_apps = app_window_titles(vm.boot_titles)
    clean = report(not boot_apps, f"no application windows at boot (saw {boot_apps})")
    panel = expect(vm, [(900, 1070, PANEL_RGB, 30)], "panel alive", tmp_path)
    launched = launch_app(vm, "files", FILES_TITLE)
    vm.screenshot(vm.out_dir / "files.png")
    visible = launched and expect(vm, [(*FILES_BODY_XY, FILES_BODY_RGB, 10)],
                                  "Files window on screen", tmp_path)
    return clean and panel and launched and visible


def probe_el0_window(vm, tmp_path: Path):
    """Screendump for ~10s until the EL0 window shows; returns (hits, image)."""
    hits, img = 0, None
    for probe in range(12):
        img = load_image(vm, tmp_path / "lb-el0-try.ppm")
        hits = count_gradient_px(img)
        print(f"  probe {probe}: {hits} px")
        if hits > EL0_RENDER_MIN_PX:
            break
        time.sleep(0.8)
    return hits, img


def start_el0_window(vm, tmp_path: Path):
    """Run the foreground GUI app (typed, not awaited) until its window renders."""
    for attempt in range(3):
        # `run /sd/guiapp` keeps the console until the app exits, so the
        # line is typed and the screen is polled instead of using shell().
        vm.send_line("run /sd/guiapp")
        time.sleep(0.5)
        hits, img = probe_el0_window(vm, tmp_path)
        if hits > EL0_RENDER_MIN_PX:
            report(True, f"EL0 window renders ({hits} gradient px, attempt {attempt + 1})")
            return img
        print(f"[retry] EL0 window not visible ({hits} px, attempt {attempt + 1})")
    print("[FAIL] EL0 window never rendered")
    return None


def click_el0_window(vm, tmp_path: Path, height: int) -> int:
    """Click the window; poll ~12s for the white cross the app draws."""
    vm.click(EL0_CLICK_XY[0], min(EL0_CLICK_XY[1], height - 40))
    whites = 0
    for _ in range(8):
        time.sleep(1.5)
        whites = count_white_px(load_image(vm, tmp_path / "lb-el0-post.ppm"))
        if whites > EL0_CLICK_MIN_WHITE_PX:
            break
    return whites


def wait_el0_exit(vm, tmp_path: Path) -> None:
    """The 600-frame demo takes ~2 minutes under TCG; wait for its window to go."""
    for _ in range(150):
        img = load_image(vm, tmp_path / "lb-el0-done.ppm")
        if count_gradient_px(img) < EL0_GONE_MAX_PX:
            return
        time.sleep(1.0)


def test_el0_app(vm, tmp_path: Path) -> bool:
    """Start the EL0 GUI app, verify its window renders, click it."""
    time.sleep(1.0)
    img = start_el0_window(vm, tmp_path)
    if img is None:
        return False
    time.sleep(1.5)  # let the app reach steady state before clicking
    whites = click_el0_window(vm, tmp_path, img.size[1])
    clicked = report(whites > EL0_CLICK_MIN_WHITE_PX,
                     f"EL0 app received the click ({whites} white cross px)")
    wait_el0_exit(vm, tmp_path)
    return clicked


def test_bg_app(vm, tmp_path: Path) -> bool:
    """S2: `run <path> &` spawns a sliced background EL0 app."""
    el0_before = serial_count(vm, b"'EL0 App'")
    vm.shell("run /sd/guiapp &", timeout=SHELL_READY_TIMEOUT_S)
    time.sleep(2.0)

    before = serial_count(vm, UPTIME_MARKER)
    vm.send_line("uptime")
    shell_ok = report(wait_count_above(vm, UPTIME_MARKER, before, 15.0),
                      "shell responds during bg app")

    el0_windows = serial_count(vm, b"'EL0 App'") - el0_before
    one_window = report(el0_windows == 1, f"bg app created {el0_windows} window (want 1)")

    for _ in range(150):
        vm.screenshot(tmp_path / "lb-bg-done.ppm")
        if serial_count(vm, EXITED_MARKER) > 0:
            break
        time.sleep(1.0)
    exited = report(serial_count(vm, EXITED_MARKER) > 0, "bg app ran to completion")
    return shell_ok and one_window and exited


def test_python(vm, tmp_path: Path) -> bool:
    """B8: python runs the embedded MicroPython REPL as an EL0 process."""
    vm.send_line("python")
    repl_offset = vm.wait_serial(b">>>", 20.0)
    vm.send_line("print(sum(i*i for i in range(10)))")
    try:
        vm.wait_serial(b"285", 15.0, since_offset=repl_offset)  # sum of squares 0..9
        ok = True
    except VmError:
        ok = False
    report(ok, "python executed code (285)")
    vm.type_serial(CTRL_D)  # EOF leaves the REPL
    time.sleep(2.0)
    vm.send_line("")
    return ok


def test_calc_keys(vm, tmp_path: Path) -> bool:
    """B6.2 + Q1a regression: Calculator accepts keyboard input AND 5 % 0 must not panic."""
    boot_banners = serial_count(vm, b"initialization complete")

    if not launch_app(vm, "calculator", CALC_TITLE):
        return False
    vm.click(*CALC_TITLE_XY)  # focus the Calculator window
    time.sleep(0.5)

    vm.key(KEY_SC["5"])
    vm.key(KEY_SC["5"], mods=PERCENT_MODS)
    vm.key(KEY_SC["0"])
    vm.key(KEY_SC["enter"])
    time.sleep(1.5)
    vm.screenshot(vm.out_dir / "calculator.png")

    ensure_shell(vm)
    display = calc_display(vm)
    shows_error = report(display == CALC_ERROR_DISPLAY,
                         f"calc 5%0: display is {CALC_ERROR_DISPLAY!r} (got {display!r})")
    # The kernel must still be alive: a panic restart would add a boot banner.
    alive = serial_count(vm, b"initialization complete") == boot_banners
    return report(alive, "calc 5%0: no kernel panic (banner count stable)") and shows_error


def calc_display(vm):
    """Read the calculator display text from the guest's `calc` shell command."""
    output = vm.shell("calc", timeout=APP_LAUNCH_TIMEOUT_S)
    for line in output.splitlines():
        if CALC_STATE_PREFIX in line:
            return line.split(CALC_STATE_PREFIX, 1)[1].strip()
    return None


def test_ctrl_c(vm, tmp_path: Path) -> bool:
    """Q2/Q6 regression: Ctrl+C chord reaches the guest."""
    if not launch_app(vm, "terminal", TERMINAL_TITLE):
        return False
    vm.click(*TERMINAL_TITLE_XY)  # focus the Terminal window
    time.sleep(0.5)
    vm.screenshot(vm.out_dir / "terminal.png")
    banner_only = report(
        count_text_lines(load_image(vm, tmp_path / "lb-term.ppm"), TERMINAL_BODY)
        <= TERMINAL_BANNER_MAX_LINES,
        "Terminal shows only its banner (no kernel log mirrored)")

    # Half-typed line, then Ctrl+C aborts it.
    for letter in "hel":
        vm.key(KEY_SC[letter])
    vm.key(KEY_SC["c"], mods="ctrl")
    time.sleep(1.0)
    vm.send_line("")
    try:
        ensure_shell(vm)  # the guest must answer 'uptime' again
        ok = True
    except VmError as error:
        print(f"      {error}")
        ok = False
    return report(ok, "Ctrl+C chord reaches guest; shell still alive") and banner_only


def test_pkg_install(vm, tmp_path: Path) -> bool:
    """B6.4: pkg install downloads a real package, installed app must run."""
    vm.shell("pkg install hello", timeout=PKG_INSTALL_TIMEOUT_S)
    installed = report(serial_count(vm, b"installed successfully") > 0,
                       "pkg install hello (download+verify+extract)")
    if not installed:
        return False
    spawn_offset = len(vm.serial_bytes())
    vm.shell("run /apps/hello &", timeout=SHELL_READY_TIMEOUT_S)
    try:
        vm.wait_serial(b"'Hello App'", 30.0, since_offset=spawn_offset)
        ran = True
    except VmError:
        ran = False
    report(ran, "installed app launches (Hello App window)")
    wait_hello_exit(vm, spawn_offset)
    return ran


def wait_hello_exit(vm, spawn_offset: int) -> None:
    """Let the short demo (150 frames) finish before later tests."""
    try:
        vm.wait_serial(EXITED_MARKER, 120.0, since_offset=spawn_offset)
    except VmError as error:
        print(f"[harness] hello app did not report exit: {error}")


def resize_to_target(vm, tmp_path: Path):
    """Poll screendumps until the fb is resized and the panel repainted.

    The shell prompt returns as soon as the mode switch is requested; the
    desktop repaint (panel re-anchor) follows a few frames later, so a
    single immediate screendump sees the new size but a stale panel.
    """
    deadline = time.monotonic() + RESIZE_SETTLE_TIMEOUT_S
    img = load_image(vm, tmp_path / "lb-res.ppm")
    while time.monotonic() < deadline:
        panel_ok = img.getpixel(RESIZED_PANEL_XY) == PANEL_RGB if img.size == RESIZED_SIZE else False
        if panel_ok:
            break
        time.sleep(0.5)
        img = load_image(vm, tmp_path / "lb-res.ppm")
    return img


def test_resolution(vm, tmp_path: Path) -> bool:
    """Type 'resolution 1280 720', expect fb resize and panel re-anchor."""
    img = None
    for attempt in range(3):
        vm.shell("resolution 1280 720", timeout=SHELL_READY_TIMEOUT_S)
        img = resize_to_target(vm, tmp_path)
        if img.size == RESIZED_SIZE:
            break
        print(f"[retry] fb still {img.size} (attempt {attempt + 1})")
    ok_size = report(img.size == RESIZED_SIZE,
                     f"framebuffer resized to {img.size} (want 1280x720)")
    panel_ok = report(img.getpixel(RESIZED_PANEL_XY) == PANEL_RGB,
                      "panel re-anchored at new bottom")
    return ok_size and panel_ok


def test_welcome_once(vm, tmp_path: Path) -> bool:
    """The Welcome window is persisted as seen: a 2nd boot of the same disk skips it."""
    first = report(WELCOME_FIRST_BOOT_MARKER in vm.serial_bytes(),
                   "first boot showed the Welcome window")
    vm.restart_clean()
    serial = vm.serial_bytes()
    skipped = report(WELCOME_SKIPPED_MARKER in serial and WELCOME_FIRST_BOOT_MARKER not in serial,
                     "second boot of the same disk skips Welcome")
    ensure_shell(vm)
    no_window = report(WELCOME_TITLE not in parse_window_titles(serial) and not vm.welcome_open(),
                       "no Welcome window exists on the second boot (log and `wins`)")
    vm.screenshot(vm.out_dir / "boot-second.png")
    return first and skipped and no_window


# Order: el0_app first (pristine post-boot flow), then GUI-click tests,
# then bg_app after el0's app has exited, resolution late because it
# shrinks the fb (calc_keys/ctrl_c do not sample the lower screen).
TESTS = {
    "el0_app": test_el0_app,
    "start_menu": test_start_menu,
    "start_menu_esc": test_start_menu_esc,
    "clock": test_panel_clock,
    "apps": test_apps_alive,
    "bg_app": test_bg_app,
    "python": test_python,
    "pkg_install": test_pkg_install,
    "resolution": test_resolution,
    "calc_keys": test_calc_keys,
    "ctrl_c": test_ctrl_c,
    "welcome_once": test_welcome_once,  # last: reboots the VM
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
    """Throwaway VM on a qcow2 overlay, wrapping lobstervm.Machine.

    Input helpers use the legacy harness pacing so the guest has time to
    consume each event.
    """

    def __init__(self, name: str, kernel: str, display: str = "none",
                 out_dir: Path = Path("tools/gui_test_out")):
        self.name = name
        self.kernel = kernel
        self.display = display
        self.out_dir = Path(out_dir)
        self.store = VmStore()
        self.machine = None
        self.tmpdir = None
        self.boot_titles = []

    def start(self) -> int:
        """Create the throwaway VM with a qcow2 overlay and boot to a clean desktop."""
        self.tmpdir = Path(tempfile.mkdtemp(prefix=f"harness-{self.name}-"))
        self.out_dir.mkdir(parents=True, exist_ok=True)
        print(f"[harness] Scratch dir: {self.tmpdir}")
        self.store.create(VmConfig(
            name=self.name, memory_gb=4, resolution="1920x1080",
            display=self.display, audio=False, kernel=self.kernel, extra_args=[]))
        self.machine = Machine(self.name, self.store)
        pid = self._boot()
        self._settle_welcome()
        return pid

    def _boot(self) -> int:
        """Boot the VM's disk to the desktop and record the windows it opened."""
        pid = self.machine.start(display=self.display)
        print(f"[harness] Started VM {self.name} (pid {pid})")
        self.machine.wait_serial(DESKTOP_READY_MARKER, BOOT_TIMEOUT_S)
        time.sleep(1.0)  # one extra desktop frame before driving the UI
        self._pass_greeter()
        self.boot_titles = parse_window_titles(self.machine.serial_bytes())
        return pid

    def restart_clean(self) -> None:
        """Clean guest shutdown, then boot the same disk again (no SIGKILL)."""
        self.machine.stop()
        if self.machine.is_running():
            raise VmError(f"VM {self.name} did not shut down")
        self._boot()

    def welcome_open(self) -> bool:
        """Ask the guest (`wins`) whether the Welcome window exists.

        The QEMU build registers the window but paints nothing for it, so
        guest state, not pixels, is the evidence.
        """
        return WELCOME_TITLE in self.machine.shell("wins", WINS_TIMEOUT_S)

    def _close_welcome(self) -> None:
        self.click(*WELCOME_CLOSE_XY)
        for _ in range(WELCOME_CLOSE_POLLS):
            if not self.welcome_open():
                return
            time.sleep(1.0)
        raise VmError("Welcome window still open after clicking its close button")

    def _settle_welcome(self) -> None:
        """Save the raw first-boot desktop, close Welcome if shown, save the clean one."""
        shown = WELCOME_TITLE in self.boot_titles
        if shown:
            ensure_shell(self)
            if not self.welcome_open():
                raise VmError("guest created Welcome but `wins` does not list it")
        self.machine.screenshot(self.out_dir / "boot-clean-raw.png")
        if shown:
            self._close_welcome()
        self.machine.screenshot(self.out_dir / "boot-clean.png")

    def _pass_greeter(self) -> None:
        """C5 greeter: with multi-user disks boot blocks at a login card."""
        for _ in range(10):
            serial = self.machine.serial_bytes()
            if LOGIN_WAIT_MARKER in serial or b"initialization complete" in serial:
                break
            time.sleep(1.0)
        serial = self.machine.serial_bytes()
        if LOGIN_WAIT_MARKER not in serial or b"[login]" in serial:
            return
        self.machine.hmp("lmouse 960 530", expect_reply=False)
        time.sleep(0.3)
        self.machine.hmp("lclick 1", expect_reply=False)
        time.sleep(0.15)
        self.machine.hmp("lclick 0", expect_reply=False)
        time.sleep(2.0)

    def stop(self) -> None:
        """Power off and delete the throwaway VM; always removes its files."""
        try:
            self._power_off()
        finally:
            self._remove_files()

    def _power_off(self) -> None:
        if self.machine is None:
            return
        try:
            if self.machine.is_running():
                self.machine.poweroff()
        except (VmError, OSError) as error:
            print(f"[harness] power-off of {self.name} failed: {error}")

    def _remove_files(self) -> None:
        try:
            self.store.delete(self.name)
        except (VmError, OSError) as error:
            print(f"[harness] could not delete VM {self.name}: {error}")
        if self.machine is not None:
            shutil.rmtree(self.machine.runtime_dir, ignore_errors=True)
        if self.tmpdir is not None:
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    def serial_bytes(self) -> bytes:
        return self.machine.serial_bytes()

    def type_serial(self, text: str) -> None:
        self.machine.type_serial(text)

    def send_line(self, text: str) -> None:
        self.machine.send_line(text)

    def wait_serial(self, marker: bytes, timeout: float, since_offset: int = 0) -> int:
        return self.machine.wait_serial(marker, timeout, since_offset)

    def shell(self, command: str, timeout: float = 60.0) -> str:
        return self.machine.shell(command, timeout)

    def click(self, x: int, y: int) -> None:
        self.machine.mouse_move(x, y)
        time.sleep(MOVE_SETTLE_S)
        self.machine.mouse_button(True, MOUSE_LEFT)
        time.sleep(CLICK_HOLD_S)
        self.machine.mouse_button(False, MOUSE_LEFT)
        time.sleep(CLICK_SETTLE_S)

    def key(self, scancode: int, mods: str = "") -> None:
        self.machine.key(scancode, mods)
        time.sleep(KEY_SETTLE_S)

    def screenshot(self, path: Path) -> None:
        self.machine.screenshot(path)


def run_one_test(vm: HarnessVM, name: str, junit: JUnitBuilder) -> bool:
    """Run one test from a known guest state and record it in the JUnit report."""
    start = time.time()
    try:
        ensure_shell(vm)
        result = TESTS[name](vm, vm.tmpdir)
        message = "" if result else f"Test {name} failed"
    except (VmError, OSError) as error:
        print(f"[FAIL] {name}: {error}")
        result, message = False, str(error)
    junit.add_result(name, result, time.time() - start, message)
    return result


def run_tests(args) -> int:
    """Run the selected tests and produce JUnit XML."""
    harness_name = f"harness-{uuid.uuid4().hex[:8]}"
    junit = JUnitBuilder()
    vm = HarnessVM(harness_name, kernel=args.kernel, display=args.display,
                   out_dir=Path(args.output_dir))
    names = list(TESTS) if args.test == "all" else [args.test]
    results = {}
    try:
        try:
            vm.start()
        except (VmError, OSError) as error:
            print(f"[FAIL] VM startup: {error}")
            junit.add_result("vm_startup", False, 0.0, str(error))
            return finish(junit, args, results, startup_failed=True)
        for name in names:
            results[name] = run_one_test(vm, name, junit)
    finally:
        vm.stop()
    return finish(junit, args, results)


def finish(junit: JUnitBuilder, args, results: dict, startup_failed: bool = False) -> int:
    junit.write(Path(args.junit))
    print(f"\nJUnit results written to {args.junit}")
    if startup_failed:
        return 1
    passed = sum(1 for v in results.values() if v)
    print(f"\n{passed}/{len(results)} tests passed")
    if passed != len(results):
        for name, result in results.items():
            print(f"  [{'PASS' if result else 'FAIL'}] {name}")
    return 0 if passed == len(results) else 1


def main():
    ap = argparse.ArgumentParser(description="LobsterOS GUI test harness on lobstervm")
    ap.add_argument("--kernel", default=DEFAULT_KERNEL,
                    help="kernel8.img to boot (default: bundled)")
    ap.add_argument("--display", choices=["none", "auto", "gtk", "sdl", "cocoa", "vnc"], default="none",
                    help="QEMU display backend (default: none)")
    ap.add_argument("--test", default="all",
                    choices=["all"] + list(TESTS),
                    help="Run a single test or all (default: all)")
    ap.add_argument("--junit", default="tools/gui_test_results.xml",
                    help="Path to write JUnit XML results (default: tools/gui_test_results.xml)")
    ap.add_argument("--output-dir", default="tools/gui_test_out",
                    help="Directory for screenshots (default: tools/gui_test_out)")
    ap.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = ap.parse_args()

    if Image is None:
        print(PILLOW_MISSING_MESSAGE, file=sys.stderr)
        return 2
    return run_tests(args)


if __name__ == "__main__":
    sys.exit(main())
