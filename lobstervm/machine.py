"""Control one VM: start/stop, monitor commands, serial console, input, snapshots.

A running VM is a detached QEMU process plus a runtime directory:

    monitor.sock   HMP monitor (unix socket)
    serial.in      FIFO feeding the guest's serial console
    serial.log     everything the guest printed on serial
    qemu.log       QEMU's own stderr
    qemu.pid       QEMU's pid

Because all state is on disk, any process (CLI, GUI, test harness) can attach
to a VM that another process started.
"""
import os
import re
import signal
import socket
import subprocess
import time
from pathlib import Path

from . import paths
from .config import VmError, VmStore

HMP_PROMPT = b"(qemu) "
HMP_TIMEOUT_S = 30.0
SHELL_PROMPT_PATTERN = re.compile(rb"lobster:[^\r\n]*# $")
DESKTOP_READY_MARKER = b"desktop-ready"
LOGIN_WAIT_MARKER = b"waiting for login"
BOOT_TIMEOUT_S = 90.0
STOP_TIMEOUT_S = 20.0
POLL_INTERVAL_S = 0.2
MOUSE_LEFT = 1
GTK_OPTIONS = "gtk,show-tabs=off,show-cursor=off,grab-on-hover=on"
VNC_DISPLAY = "vnc=127.0.0.1:9"


class Machine:
    """Handle to a VM defined in a :class:`VmStore`."""

    def __init__(self, name, store=None):
        self.store = store or VmStore()
        self.name = name
        self.config = self.store.load(name)
        self.runtime_dir = paths.runtime_home() / name
        self.monitor_path = self.runtime_dir / "monitor.sock"
        self.serial_in_path = self.runtime_dir / "serial.in"
        self.serial_log_path = self.runtime_dir / "serial.log"
        self.qemu_log_path = self.runtime_dir / "qemu.log"
        self.pid_path = self.runtime_dir / "qemu.pid"

    # ── lifecycle ────────────────────────────────────────────────────

    def pid(self):
        """PID of the running QEMU, or None when the VM is stopped."""
        try:
            pid = int(self.pid_path.read_text().strip())
        except (FileNotFoundError, ValueError):
            return None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            return pid
        return pid if _is_qemu(pid) else None

    def is_running(self):
        return self.pid() is not None

    def start(self, display=None, resume=None):
        """Boot the VM detached. ``resume`` restores a snapshot tag first."""
        if self.is_running():
            raise VmError(f"VM {self.name} is already running (pid {self.pid()})")
        self._prepare_runtime_dir()
        if not self.store.disk_path(self.name).exists():
            self.store.reset_disk(self.name)
        command = self._qemu_command(display or self.config.display, resume)
        serial_in = os.open(self.serial_in_path, os.O_RDWR)
        with open(self.serial_log_path, "wb") as serial_log, \
                open(self.qemu_log_path, "wb") as qemu_log:
            process = subprocess.Popen(
                command, env=self._qemu_env(), stdin=serial_in,
                stdout=serial_log, stderr=qemu_log, start_new_session=True)
        os.close(serial_in)
        self.pid_path.write_text(f"{process.pid}\n")
        self._wait_for_monitor(process)
        return process.pid

    def wait_desktop(self, timeout=BOOT_TIMEOUT_S):
        """Block until the guest reports its desktop is up; returns boot ms."""
        self._wait_serial(DESKTOP_READY_MARKER, 0, timeout)
        match = re.search(rb"desktop-ready t_ms=(\d+)", self.serial_bytes())
        return int(match.group(1)) if match else None

    def stop(self, timeout=STOP_TIMEOUT_S):
        """Ask the guest to shut down cleanly; fall back to power-off."""
        if not self.is_running():
            return
        self.type_serial("shutdown\n")
        if not self._wait_exit(timeout):
            self.poweroff()

    def poweroff(self):
        """Pull the plug: quit QEMU immediately."""
        pid = self.pid()
        if pid is None:
            return
        try:
            self.hmp("quit", expect_reply=False)
        except (VmError, OSError):
            pass
        if not self._wait_exit(5.0):
            os.kill(pid, signal.SIGKILL)
            self._wait_exit(5.0)
        self.pid_path.unlink(missing_ok=True)

    def reset(self):
        self.hmp("system_reset")

    def pause(self):
        self.hmp("stop")

    def resume(self):
        self.hmp("cont")

    def status(self):
        if not self.is_running():
            return "stopped"
        reply = self.hmp("info status")
        return "paused" if "paused" in reply else "running"

    # ── monitor ──────────────────────────────────────────────────────

    def hmp(self, line, expect_reply=True):
        """Run one HMP monitor command and return its text output."""
        if not self.is_running():
            raise VmError(f"VM {self.name} is not running")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(HMP_TIMEOUT_S)
            sock.connect(str(self.monitor_path))
            _read_until_prompt(sock)
            sock.sendall(line.encode() + b"\n")
            if not expect_reply:
                return ""
            reply = _read_until_prompt(sock)
        return _strip_hmp_echo(reply)

    # ── snapshots ────────────────────────────────────────────────────

    def snapshot_take(self, tag):
        reply = self.hmp(f"savevm {tag}")
        if "Error" in reply or "rror:" in reply:
            raise VmError(f"snapshot failed: {reply.strip()}")

    def snapshot_restore(self, tag):
        reply = self.hmp(f"loadvm {tag}")
        if "Error" in reply or "rror:" in reply:
            raise VmError(f"restore failed: {reply.strip()}")

    def snapshot_list(self):
        if self.is_running():
            return _parse_snapshot_table(self.hmp("info snapshots"))
        return self.store.snapshots(self.name)

    def snapshot_delete(self, tag):
        if self.is_running():
            self.hmp(f"delvm {tag}")
        else:
            self.store.delete_snapshot(self.name, tag)

    # ── display + input ──────────────────────────────────────────────

    def screenshot(self, path):
        """Write the guest framebuffer to ``path`` (.png or .ppm)."""
        path = Path(path).resolve()
        fmt = " -f png" if path.suffix.lower() == ".png" else ""
        path.unlink(missing_ok=True)
        self.hmp(f"screendump {path}{fmt}")
        if not path.exists():
            raise VmError(f"screenshot was not written to {path}")
        return path

    def mouse_move(self, x, y):
        self.hmp(f"lmouse {int(x)} {int(y)}")

    def mouse_button(self, pressed, button=MOUSE_LEFT):
        self.hmp(f"lclick {button if pressed else 0}")

    def click(self, x, y, button=MOUSE_LEFT, hold_s=0.15):
        self.mouse_move(x, y)
        self.mouse_button(True, button)
        time.sleep(hold_s)
        self.mouse_button(False, button)

    def double_click(self, x, y):
        self.click(x, y, hold_s=0.05)
        time.sleep(0.08)
        self.click(x, y, hold_s=0.05)

    def drag(self, x0, y0, x1, y1, steps=8):
        self.mouse_move(x0, y0)
        self.mouse_button(True)
        for step in range(1, steps + 1):
            self.mouse_move(x0 + (x1 - x0) * step // steps,
                            y0 + (y1 - y0) * step // steps)
        self.mouse_button(False)

    def key(self, scancode, mods=""):
        """Press+release a Set-1 scancode; ``mods`` like "ctrl shift"."""
        self.hmp(f"lkey {scancode:#x} {mods}".rstrip())

    # ── serial console ───────────────────────────────────────────────

    def serial_bytes(self):
        try:
            return self.serial_log_path.read_bytes()
        except FileNotFoundError:
            return b""

    def type_serial(self, text):
        """Send raw text to the guest's serial console."""
        if not self.is_running():
            raise VmError(f"VM {self.name} is not running")
        fd = os.open(self.serial_in_path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, text.encode())
        finally:
            os.close(fd)

    def shell(self, command, timeout=60.0):
        """Run a guest shell command over serial; return what it printed."""
        offset = len(self.serial_bytes())
        self.type_serial(command + "\n")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            output = self.serial_bytes()[offset:]
            echoed = output.find(command.encode())
            if echoed >= 0 and SHELL_PROMPT_PATTERN.search(output[echoed:]):
                return _shell_output(output[echoed:], command)
            time.sleep(POLL_INTERVAL_S)
        raise VmError(f"guest shell did not finish {command!r} in {timeout:.0f}s")

    # ── internals ────────────────────────────────────────────────────

    def _prepare_runtime_dir(self):
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        for stale in (self.monitor_path, self.serial_in_path, self.pid_path):
            stale.unlink(missing_ok=True)
        os.mkfifo(self.serial_in_path, 0o600)

    def _qemu_command(self, display, resume):
        config = self.config
        disk = self.store.disk_path(self.name)
        command = [
            str(paths.qemu_binary()),
            "-M", f"{config.machine_type},graphics=on",
            "-m", f"{config.memory_gb}G",
            "-cpu", "cortex-a76",
            "-kernel", str(config.kernel_path),
            "-dtb", str(paths.default_dtb()),
            "-drive", f"if=none,id=lobsterdisk,file={disk},format=qcow2",
            "-serial", "stdio",
            "-monitor", f"unix:{self.monitor_path},server,nowait",
            "-display", _display_argument(display),
        ]
        if not config.audio or display == "none":
            command += ["-audio", "none"]
        if resume:
            command += ["-loadvm", resume]
        return command + list(config.extra_args)

    def _qemu_env(self):
        env = dict(os.environ)
        env["LBST_RES"] = self.config.resolution
        env.pop("LBST_DISKIMG", None)
        if self.config.shared_folder:
            env["LBST_SHARE"] = self.config.shared_folder
        else:
            env.pop("LBST_SHARE", None)
        return env

    def _wait_for_monitor(self, process):
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if process.poll() is not None:
                tail = self.qemu_log_path.read_text(errors="replace")[-600:]
                self.pid_path.unlink(missing_ok=True)
                raise VmError(f"QEMU exited during startup:\n{tail}")
            if self.monitor_path.exists():
                return
            time.sleep(POLL_INTERVAL_S)
        raise VmError("QEMU monitor socket never appeared")

    def _wait_serial(self, marker, offset, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if marker in self.serial_bytes()[offset:]:
                return
            if not self.is_running():
                raise VmError(f"VM {self.name} exited while waiting for {marker!r}")
            time.sleep(POLL_INTERVAL_S)
        raise VmError(f"timed out after {timeout:.0f}s waiting for {marker!r} on serial")

    def _wait_exit(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_running():
                self.pid_path.unlink(missing_ok=True)
                return True
            time.sleep(POLL_INTERVAL_S)
        return False


def _is_qemu(pid):
    """Guard against pid reuse: the pid must still be a QEMU process."""
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return b"qemu-system" in cmdline and state != "Z"


def _display_argument(display):
    if display == "gtk":
        return GTK_OPTIONS
    if display == "vnc":
        return VNC_DISPLAY
    return display


def _read_until_prompt(sock):
    data = b""
    while not data.endswith(HMP_PROMPT):
        try:
            chunk = sock.recv(65536)
        except socket.timeout as error:
            raise VmError("QEMU monitor did not answer") from error
        if not chunk:
            break
        data += chunk
    return data


def _strip_hmp_echo(reply):
    """Drop the echoed command line (with its readline escapes) and prompt."""
    text = reply.decode("utf-8", "replace")
    if text.endswith(HMP_PROMPT.decode()):
        text = text[: -len(HMP_PROMPT)]
    _, _, body = text.partition("\n")
    return body.replace("\r", "")


def _parse_snapshot_table(table):
    tags = []
    for line in table.splitlines():
        parts = line.split()
        if len(parts) >= 2 and (parts[0].isdigit() or parts[0] == "--"):
            tags.append(parts[1])
    return tags


def _shell_output(raw, command):
    """Text between the echoed command line and the next shell prompt."""
    text = raw.decode("utf-8", "replace").replace("\r", "")
    _, _, after_echo = text.partition(command)
    body, _, _ = after_echo.rpartition("\n")
    return body.strip("\n")
