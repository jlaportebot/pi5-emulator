#!/usr/bin/env python3
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
            "-L", "/usr/share/qemu", "-vnc", ":9",
            "-monitor", f"unix:{self.sock_path},server,nowait",
        ]
        self.stderr_path = "/tmp/lb-guitest-qemu.log"
        self._stderr = open(self.stderr_path, "wb")
        self.proc = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.DEVNULL, stderr=self._stderr)
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
        time.sleep(12)  # boot to desktop
        self.mon = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.mon.connect(self.sock_path)
        self.mon.settimeout(5)
        try:
            self.mon.recv(65536)
        except OSError:
            pass

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
    """checks: list of (x, y, (r,g,b), tol). Returns (ok, details)."""
    path = "/tmp/lb-expect.ppm"
    vm.dump(path)
    from PIL import Image
    img = Image.open(path).convert("RGB")
    fails = []
    for (x, y, rgb, tol) in checks:
        got = img.getpixel((x, y))
        if all(abs(got[i] - rgb[i]) <= tol for i in range(3)):
            continue
        fails.append(f"({x},{y}) want≈{rgb} got {got}")
    status = "PASS" if not fails else "FAIL"
    print(f"[{status}] {label}")
    for f in fails:
        print("      " + f)
    return not fails


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
    """Click the terminal, type 'resolution 1280 720', expect the
    screendump to become 1280x720 with a live panel."""
    from PIL import Image
    for attempt in range(3):
        vm.click(400, 70)              # focus terminal via title bar
        time.sleep(0.5)
        type_string(vm, "resolution 1280 720", per_key=0.22)
        vm.key(0x1C)                   # Enter (set-1)
        time.sleep(3)
        vm.dump("/tmp/lb-res.ppm")
        img = Image.open("/tmp/lb-res.ppm").convert("RGB")
        if img.size == (1280, 720):
            break
    ok_size = img.size == (1280, 720)
    print(f"[{'PASS' if ok_size else 'FAIL'}] framebuffer resized to {img.size} (want 1280x720)")
    panel_ok = img.getpixel((640, 700)) == (13, 27, 42)
    print(f"[{'PASS' if panel_ok else 'FAIL'}] panel re-anchored at new bottom")
    return ok_size and panel_ok


def test_el0_app(vm):
    """Start the EL0 GUI app, verify its window renders, click it, and
    verify the content changes (interactive user-space process)."""
    from PIL import Image
    S1 = {'r':0x13,'u':0x16,'n':0x31,' ':0x39,'/':0x35,'s':0x1F,'d':0x20,
          'g':0x22,'i':0x17,'a':0x1E,'p':0x19}
    ok = False
    for attempt in range(3):
        vm.click(400, 70)             # focus terminal
        time.sleep(0.5)
        for ch in "run /sd/guiapp":
            vm.key(S1[ch])
            time.sleep(0.2)
        vm.key(0x1C)                  # Enter
        time.sleep(1.5)
        vm.dump("/tmp/lb-el0-pre.ppm")
        img = Image.open("/tmp/lb-el0-pre.ppm").convert("RGB")
        W, H = img.size
        def colorful(px):
            c = img.getpixel(px)
            return (c not in [(30,30,46),(0,0,0),(56,63,79)]
                    and abs(c[0]-c[1]) + abs(c[1]-c[2]) > 50)
        hits = sum(1 for y in range(630, min(810, H - 2), 3)
                   for x in range(70, min(340, W - 2), 3)
                   if colorful((x, y)))
        if hits > 2000:
            print(f"[PASS] EL0 window renders ({hits} gradient px, attempt {attempt+1})")
            ok = True
            break
        print(f"[retry] EL0 window not visible yet ({hits} px)")
    if not ok:
        print("[FAIL] EL0 window never rendered")
        return False

    # Click the window and check the visible bands change
    def bands(path):
        im = Image.open(path).convert("RGB")
        W, H = im.size
        ys = [y for y in range(636, min(636 + 6 * 28, H - 2), 28)]
        return [im.getpixel((min(200, W - 2), y)) for y in ys]
    pre = bands("/tmp/lb-el0-pre.ppm")
    vm.click(200, min(715, H - 40))
    time.sleep(1.0)
    vm.dump("/tmp/lb-el0-post.ppm")
    post = bands("/tmp/lb-el0-post.ppm")
    changed = sum(1 for a, b in zip(pre, post) if a != b)
    need = max(2, len(pre) // 2)
    print(f"[{'PASS' if changed >= need else 'FAIL'}] EL0 app responded to click ({changed}/{len(pre)} bands changed)")
    return changed >= need


TESTS = {
    "start_menu": test_start_menu,
    "clock": test_panel_clock,
    "apps": test_apps_alive,
    "resolution": test_resolution,
    "el0_app": test_el0_app,
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
