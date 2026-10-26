#!/usr/bin/env python3
# Prefer the dist-packages Pillow (the active venv's PIL has a stale
# binary for the current interpreter).
import sys
sys.path.insert(0, "/usr/lib/python3/dist-packages")
"""
LobsterOS GUI test harness (E1)

Boots the emulator headless with a QEMU monitor socket, drives the
desktop with HMP input commands (mouse_move / mouse_button / sendkey),
captures screendumps, and asserts pixel conditions with PIL.

Usage:
  python3 gui_test.py [--kernel path] [--disk path] [--test name]

Available tests live in TESTS; each is a generator of steps where a
step is one of:
  ("move", x, y)        — move host mouse
  ("click", x, y)       — move + press/release button 1
  ("key", "ctrl-t")     — HMP sendkey
  ("wait", secs)        — sleep
  ("dump", path)        — screendump
  ("expect", [(x, y), (r, g, b, tol)]) — assert pixel ≈ color
"""
import argparse
import os
import socket
import subprocess
import sys
import time

QEMU = "/mnt/data/sd-overflow/qemu-src/build-opt/qemu-system-aarch64"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_KERNEL = os.path.join(HERE, "kernel8.img")
DEFAULT_DTB = os.path.join(HERE, "bcm2712-rpi-5-b.dtb")
DEFAULT_DISK = os.path.join(HERE, "lobsteros.img")


class VM:
    def __init__(self, kernel=DEFAULT_KERNEL, disk=DEFAULT_DISK, share=None, audio=None):
        self.sock_path = "/tmp/lb-guitest.sock"
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)
        env = {**os.environ, "DISPLAY": ":0", "LBST_DISKIMG": disk}
        if share:
            env["LBST_SHARE"] = share
        cmd = [
            QEMU, "-M", "raspi5b,graphics=on", "-m", "4G",
            "-cpu", "cortex-a76", "-kernel", kernel, "-dtb", DEFAULT_DTB,
            "-display", "none", "-no-reboot", "-no-shutdown",
            # VNC creates a live graphical console so HMP input injection
            # (mouse_move/mouse_button/sendkey) routes to the guest.
            "-L", "/usr/share/qemu", "-serial", "stdio",
            "-monitor", f"unix:{self.sock_path},server,nowait",
        ]
        self.stderr_path = "/tmp/lb-guitest-qemu.log"
        self._stderr = open(self.stderr_path, "wb")
        self.proc = subprocess.Popen(
            cmd, env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self._stderr)
        # Tee serial output to a log file in the background.
        import threading
        self._serial_log = open("/tmp/lb-serial-out.log", "wb")
        def _tee():
            try:
                while True:
                    b = self.proc.stdout.read(1)
                    if not b:
                        break
                    self._serial_log.write(b)
                    self._serial_log.flush()
            except OSError:
                pass
        self._tee = threading.Thread(target=_tee, daemon=True)
        self._tee.start()
        self._serial_buf = b""

        # The serial console is QEMU's stdio, connected to our stdin
        # PIPE (unix-socket serial makes QEMU's main loop crawl and
        # starves the monitor).

        # Wait for the monitor socket, then boot to desktop.
        for _ in range(40):
            if self.proc.poll() is not None:
                self._stderr.close()
                tail = open(self.stderr_path, "rb").read()[-500:]
                raise RuntimeError(
                    f"QEMU exited during startup: {tail!r}")
            try:
                time.sleep(0.5)
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(self.sock_path)
                s.close()
                break
            except OSError:
                continue
        else:
            raise RuntimeError("monitor socket never appeared")
        # Boot to desktop: wait for the kernel's desktop-ready marker instead
        # of a fixed sleep (E2). Falls back to a 60s timeout so a silent boot
        # still fails loudly instead of hanging forever.
        boot_deadline = time.time() + 60
        ready = False
        while time.time() < boot_deadline:
            if self.proc.poll() is not None:
                self._stderr.close()
                tail = open(self.stderr_path, "rb").read()[-500:]
                raise RuntimeError(
                    f"QEMU exited during startup: {tail!r}")
            try:
                with open("/tmp/lb-serial-out.log", "rb") as f:
                    if b"desktop-ready" in f.read():
                        ready = True
                        break
            except FileNotFoundError:
                pass
            time.sleep(0.5)
        if not ready:
            raise RuntimeError("desktop-ready marker never appeared on serial")
        time.sleep(1.0)  # one extra desktop frame before driving the UI
        self.mon = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.mon.connect(self.sock_path)
        self.mon.settimeout(5)
        try:
            self.mon.recv(65536)
        except OSError:
            pass

        # C5 greeter: with multi-user disks boot blocks at a login card.
        # If the greeter is up, click the first user row to log in.
        def _serial_has(marker):
            try:
                with open("/tmp/lb-serial-out.log", "rb") as f:
                    return marker in f.read().decode("utf-8", "replace")
            except FileNotFoundError:
                return False
        for _ in range(10):
            if _serial_has("waiting for login"):
                break
            if _serial_has("initialization complete"):
                break
            time.sleep(1.0)
        if _serial_has("waiting for login") and not _serial_has("[login]"):
            self.hmp("lmouse 960 530", 0.3)
            self.hmp("lclick 1", 0.15)
            self.hmp("lclick 0", 2.0)

    def send_serial(self, text, settle=0.3, wait_ready=False):
        """Type into the guest over the serial console. With wait_ready,
        first handshake with 'uptime' until the shell echoes back —
        serial input sent while the guest shell is blocked (e.g. during
        a cooperative run_and_wait) overflows the UART FIFO and is lost.
        """
        if wait_ready:
            # Real handshake: type 'uptime' until the shell echoes a
            # response — proves the shell loop is polling serial again
            # (i.e. any cooperative run_and_wait has returned).
            # The cooperative EL0 demo runs ~2 minutes under TCG (each
            # 33ms guest sleep pumps full desktop composites), so the
            # handshake must allow for it.
            deadline = time.time() + 150
            before = self._serial_count("Uptime:")
            while time.time() < deadline:
                assert self.proc.stdin is not None
                self.proc.stdin.write(b"uptime\n")
                self.proc.stdin.flush()
                time.sleep(1.0)
                if self._serial_count("Uptime:") > before:
                    break
        assert self.proc.stdin is not None
        self.proc.stdin.write(text.encode())
        self.proc.stdin.flush()
        time.sleep(settle)

    def _serial_count(self, marker):
        try:
            with open("/tmp/lb-serial-out.log", "rb") as f:
                return f.read().decode("utf-8", "replace").count(marker)
        except FileNotFoundError:
            return 0

    def hmp(self, line, wait=0.3):
        self.mon.sendall((line + "\n").encode())
        time.sleep(wait)
        try:
            self.mon.recv(65536)
        except OSError:
            pass

    def move(self, x, y):
        # Direct device injection — the QEMU console layer drops HMP
        # mouse events when no display client is attached.
        self.hmp(f"lmouse {x} {y}", 0.2)

    def click(self, x, y):
        self.move(x, y)
        self.hmp("lclick 1", 0.15)
        self.hmp("lclick 0", 0.4)

    def key(self, scancode):
        # set-1 scancode (e.g. 0x2B = Tab, 0x01 = Esc)
        self.hmp(f"lkey {scancode}", 0.25)

    def dump(self, path):
        self.hmp(f"screendump {path}", 1.2)

    def quit(self):
        try:
            self.hmp("quit", 0.5)
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def stop(self):
        self.hmp("stop", 0.5)

    def cont(self):
        self.hmp("cont", 0.5)


def expect(vm, checks, label):
    """checks: list of (x, y, (r,g,b), tol). Retries the dump up to 3
    times — screendumps can catch mid-render frames."""
    path = "/tmp/lb-expect.ppm"
    from PIL import Image
    for _ in range(3):
        vm.dump(path)
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


def test_start_menu(vm):
    """Click the launcher, expect the start menu panel (dark menu_bg +
    bright app-label text) to appear above the panel."""
    ok = False
    for attempt in range(3):
        vm.click(25, 1060)               # launcher button, bottom-left
        time.sleep(0.8)
        ok = expect(vm, [
            (150, 900, (26, 38, 52), 40),     # menu panel bg
            (150, 750, (26, 38, 52), 40),     # menu panel bg (upper)
        ], f"start menu opens on launcher click (attempt {attempt + 1})")
        if ok:
            break
    if ok:
        vm.dump("/tmp/lb-sm.ppm")
        from PIL import Image
        img = Image.open("/tmp/lb-sm.ppm").convert("RGB")
        bright = sum(1 for y in range(700, 1040, 2) for x in range(0, 400, 2)
                     if sum(img.getpixel((x, y))) > 600)
        print(f"[{'PASS' if bright > 20 else 'FAIL'}] menu labels render ({bright} px)")
        ok = ok and bright > 20
    vm.key(0x01)                    # Esc closes the menu
    time.sleep(0.5)
    return ok


def test_panel_clock(vm):
    """The panel clock must render text right of the tray."""
    ok = expect(vm, [
        (900, 1070, (13, 27, 42), 40),    # panel bg itself
    ], "panel painted")
    # find bright text pixels in the clock zone x>1700
    vm.dump("/tmp/lb-clock.ppm")
    from PIL import Image
    img = Image.open("/tmp/lb-clock.ppm").convert("RGB")
    bright = sum(1 for y in range(1050, 1075) for x in range(1700, 1920)
                 if sum(img.getpixel((x, y))) > 450)
    print(f"[{'PASS' if bright > 20 else 'FAIL'}] clock text visible ({bright} px)")
    return bright > 20


def test_apps_alive(vm):
    """Boot windows must be on screen: sysmon dark bg at (900,100)."""
    return expect(vm, [
        (900, 100, (30, 30, 46), 30),
        (900, 1070, (13, 27, 42), 30),
    ], "desktop windows + panel alive")


# set-1 scancodes for the strings the harness types
S1 = {
    'a':0x1E,'b':0x30,'c':0x2E,'d':0x20,'e':0x12,'f':0x21,'g':0x22,'h':0x23,
    'i':0x17,'j':0x24,'k':0x25,'l':0x26,'m':0x32,'n':0x31,'o':0x18,'p':0x19,
    'q':0x10,'r':0x13,'s':0x1F,'t':0x14,'u':0x16,'v':0x2F,'w':0x11,'x':0x2D,
    'y':0x15,'z':0x2C,'1':0x02,'2':0x03,'3':0x04,'4':0x05,'5':0x06,'6':0x07,
    '7':0x08,'8':0x09,'9':0x0A,'0':0x0B,' ':0x39,'/':0x35,'.':0x34,
}

def type_string(vm, text, per_key=0.12):
    for ch in text:
        vm.key(S1[ch])
        time.sleep(per_key)


def test_resolution(vm):
    """Type 'resolution 1280 720' over serial, expect the screendump to
    become 1280x720 with a live panel."""
    from PIL import Image
    vm.send_serial("\n", settle=0.2)
    img = None
    for attempt in range(3):
        vm.send_serial("resolution 1280 720\n", settle=3.0, wait_ready=True)
        vm.dump("/tmp/lb-res.ppm")
        img = Image.open("/tmp/lb-res.ppm").convert("RGB")
        if img.size == (1280, 720):
            break
        print(f"[retry] fb still {img.size} (attempt {attempt+1})")
    ok_size = img.size == (1280, 720)
    print(f"[{'PASS' if ok_size else 'FAIL'}] framebuffer resized to {img.size} (want 1280x720)")
    panel_ok = img.getpixel((640, 700)) == (13, 27, 42)
    print(f"[{'PASS' if panel_ok else 'FAIL'}] panel re-anchored at new bottom")
    return ok_size and panel_ok



def test_el0_app(vm):
    """Start the EL0 GUI app, verify its window renders, click it, and
    verify the content changes (interactive user-space process)."""
    import shutil
    from PIL import Image

    def count_hits(path):
        im = Image.open(path).convert("RGB")
        w, h = im.size

        def col(px):
            c = im.getpixel(px)
            return (c not in [(30, 30, 46), (0, 0, 0), (56, 63, 79)]
                    and abs(c[0] - c[1]) + abs(c[1] - c[2]) > 50)

        return sum(1 for y in range(630, min(810, h - 2), 3)
                   for x in range(70, min(340, w - 2), 3)
                   if col((x, y))), (w, h)

    vm.send_serial("\n", settle=1.0, wait_ready=True)  # handshake first
    rendered = False
    for attempt in range(3):
        # Spawn time varies; the window lives only ~2s after it appears.
        # Dump continuously for ~12s and accept any live-window frame.
        vm.send_serial("run /sd/guiapp\n", settle=0.5)
        for probe in range(12):
            vm.dump("/tmp/lb-el0-try.ppm")
            img = Image.open("/tmp/lb-el0-try.ppm").convert("RGB")
            W, H = img.size
            def colorful(px):
                c = img.getpixel(px)
                return (c not in [(30,30,46),(0,0,0),(56,63,79)]
                        and abs(c[0]-c[1]) + abs(c[1]-c[2]) > 50)
            hits = sum(1 for y in range(630, min(810, H - 2), 3)
                       for x in range(70, min(340, W - 2), 3)
                       if colorful((x, y)))
            print(f"  probe {probe}: {hits} px")
            if hits > 2000:
                break
            time.sleep(0.8)
        if hits > 2000:
            print(f"[PASS] EL0 window renders ({hits} gradient px, attempt {attempt+1})")
            shutil.copy("/tmp/lb-el0-try.ppm", "/tmp/lb-el0-pre.ppm")
            rendered = True
            break
        print(f"[retry] EL0 window not visible ({hits} px, attempt {attempt+1})")
    if not rendered:
        print("[FAIL] EL0 window never rendered")
        return False

    def bands(path):
        im = Image.open(path).convert("RGB")
        w, h = im.size
        ys = [y for y in range(636, min(636 + 6 * 28, h - 2), 28)]
        return [im.getpixel((min(200, w - 2), y)) for y in ys]

    # Let the app reach steady state (a few frames in) before clicking —
    # clicks in the first frames can race the SYS_WIN_CREATE registration.
    time.sleep(1.5)
    vm.click(200, min(715, H - 40))
    # The demo draws a WHITE CROSS at the click point and freezes — but
    # under TCG the app may take a few seconds to reach the frame that
    # dequeues the press and repaints the marker. Poll for up to ~12s.
    whites = 0
    for i in range(8):
        time.sleep(1.5)
        vm.dump("/tmp/lb-el0-post.ppm")
        img2 = Image.open("/tmp/lb-el0-post.ppm").convert("RGB")
        W2, H2 = img2.size
        whites = sum(1 for y in range(630, min(810, H2 - 2), 2)
                     for x in range(70, min(340, W2 - 2), 2)
                     if img2.getpixel((x, y)) == (255, 255, 255))
        if whites > 30:
            break
    print(f"[{'PASS' if whites > 30 else 'FAIL'}] EL0 app received the click ({whites} white cross px)")
    changed = 999 if whites > 30 else 0
    need = 30

    # Wait for the app to exit so later tests can use the shell.
    # Under TCG the 600-frame demo stretches to ~2 minutes of wall time
    # (every guest sleep pumps a full desktop composite), so poll long.
    for _ in range(150):
        vm.dump("/tmp/lb-el0-done.ppm")
        hits2, _ = count_hits("/tmp/lb-el0-done.ppm")
        if hits2 < 500:
            break
        time.sleep(1.0)
    return changed >= need

# Order matters: resolution shrinks the framebuffer, so tests that
# sample the lower screen run first.
# Order: el0_app needs the pristine post-boot flow (its window renders
# reliably there); GUI-click tests run after; bg_app after el0's app has
# exited; resolution last because it shrinks the framebuffer.

def test_bg_app(vm):
    """S2: `run <path> &` spawns a sliced background EL0 app — the shell
    must stay responsive while the app runs, and the app must create
    exactly one window (no restart-per-slice)."""
    vm.send_serial("\n", settle=0.5, wait_ready=True)
    el0_before = vm._serial_count(chr(39) + 'EL0 App' + chr(39))
    vm.send_serial("run /sd/guiapp &\n", settle=1.5)
    time.sleep(2.0)

    before = vm._serial_count("Uptime:")
    vm.send_serial("uptime\n", settle=0.5)
    shell_ok = False
    for _ in range(15):
        if vm._serial_count("Uptime:") > before:
            shell_ok = True
            break
        time.sleep(1.0)
    print(f"[{'PASS' if shell_ok else 'FAIL'}] shell responds during bg app")

    # One window for the app — count EL0 App windows created AFTER this
    # test started (earlier tests also create EL0 App windows).
    windows = vm._serial_count("Created window id=")
    el0_windows = vm._serial_count("'EL0 App'") - el0_before
    one_window = el0_windows == 1
    print(f"[{'PASS' if one_window else 'FAIL'}] bg app created {el0_windows} window (want 1)")

    # Wait for the app to exit so later tests get a clean shell.
    for _ in range(150):
        vm.dump("/tmp/lb-bg-done.ppm")
        if vm._serial_count("exited with code") > 0:
            break
        time.sleep(1.0)
    exited = vm._serial_count("exited with code") > 0
    print(f"[{'PASS' if exited else 'FAIL'}] bg app ran to completion")
    return shell_ok and one_window and exited


def test_python(vm):
    """B8: python runs the embedded MicroPython REPL as an EL0
    process — real Python code must execute and print output."""
    vm.send_serial("\n", settle=0.5, wait_ready=True)
    vm.send_serial("python\n", settle=4.0)
    before = vm._serial_count(">>>")
    vm.send_serial("print(sum(i*i for i in range(10)))\n", settle=3.0)
    ok = vm._serial_count("285") > 0  # sum of squares 0..9
    print(f"[{'PASS' if ok else 'FAIL'}] python executed code (285)")
    # exit the REPL cleanly (Ctrl-D = EOF)
    vm.key(0x24) if False else None
    vm.send_serial("\x04", settle=1.0)  # EOT
    time.sleep(2.0)
    vm.send_serial("\n", settle=0.5)
    return ok


def test_pkg_install(vm):
    """B6.4: pkg install downloads a real package from the GitHub
    package repository, verifies it, extracts it — and the installed
    app must then run as a background EL0 process."""
    vm.send_serial("\n", settle=0.5, wait_ready=True)
    vm.send_serial("pkg install hello\n", settle=20.0)
    installed = vm._serial_count("installed successfully") > 0
    print(f"[{'PASS' if installed else 'FAIL'}] pkg install hello (download+verify+extract)")
    if not installed:
        return False
    vm.send_serial("run /apps/hello &\n", settle=8.0)
    ran = vm._serial_count("'Hello App'") > 0
    print(f"[{'PASS' if ran else 'FAIL'}] installed app launches (Hello App window)")
    # let the short demo (150 frames) finish before later tests
    for _ in range(60):
        if vm._serial_count("bg PID") > 0 and vm._serial_count("exited with code") > 0:
            break
        time.sleep(2.0)
    return installed and ran


TESTS = {
    "el0_app": test_el0_app,
    "start_menu": test_start_menu,
    "clock": test_panel_clock,
    "apps": test_apps_alive,
    "bg_app": test_bg_app,
    "python": test_python,
    "pkg_install": test_pkg_install,
    "resolution": test_resolution,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", default=DEFAULT_KERNEL)
    ap.add_argument("--disk", default=DEFAULT_DISK)
    ap.add_argument("--share", default=None)
    ap.add_argument("--test", default="all")
    args = ap.parse_args()

    vm = VM(kernel=args.kernel, disk=args.disk, share=args.share)
    names = list(TESTS) if args.test == "all" else [args.test]
    results = {}
    try:
        for name in names:
            try:
                results[name] = TESTS[name](vm)
            except Exception as e:  # noqa: BLE001 — harness reports, not crashes
                print(f"[FAIL] {name}: {e}")
                results[name] = False
    finally:
        vm.quit()

    passed = sum(1 for v in results.values() if v)
    print(f"\n{passed}/{len(results)} tests passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
