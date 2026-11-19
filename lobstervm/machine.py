"""Control one VM: start/stop, monitor commands, serial console, input, snapshots.

A running VM is a detached QEMU process plus a runtime directory:

    monitor.sock   HMP monitor (unix socket, or monitor.port with its TCP port)
    serial.sock    guest serial console (or serial.port); QEMU also appends
                   everything the guest prints to serial.log
    serial.log     everything the guest printed on serial
    qemu.log       QEMU's own stderr
    qemu.pid       QEMU's pid

Because all state is on disk, any process (CLI, GUI, test harness) can attach
to a VM that another process started.
"""
import os
import re
import socket
import subprocess
import time
from pathlib import Path

from . import accel, paths, procctl
from .channel import Channel, Endpoint
from .config import VmError, VmStore
from .display import display_argument, resolve_display

HMP_PROMPT = b"(qemu) "
HMP_TIMEOUT_S = 30.0
# The guest shell prints "lobster:<cwd># " and does NOT terminate it with a
# newline (the cursor sits right after the prompt), so the pattern must not
# anchor on end-of-line.
SHELL_PROMPT_PATTERN = re.compile(rb"lobster:[^\r\n]*# ")
DESKTOP_READY_MARKER = b"desktop-ready"
LOGIN_WAIT_MARKER = b"waiting for login"
BOOT_TIMEOUT_S = 90.0
STOP_TIMEOUT_S = 20.0
POLL_INTERVAL_S = 0.2
MOUSE_LEFT = 1
MONITOR_CHANNEL = "monitor"
SERIAL_CHANNEL = "serial"
MONITOR_START_TIMEOUT_S = 20.0
SCREENSHOT_WRITE_TIMEOUT_S = 10.0
LOG_TAIL_CHARS = 600
POWEROFF_GRACE_S = 5.0
LEGACY_DEVICE_ENV = ("LBST_RES", "LBST_SHARE", "LBST_DISKIMG")


class Machine:
    """Handle to a VM defined in a :class:`VmStore`."""

    def __init__(self, name, store=None):
        self.store = store or VmStore()
        self.name = name
        self.config = self.store.load(name)
        self.runtime_dir = paths.runtime_home() / name
        self.monitor = Channel(self.runtime_dir, MONITOR_CHANNEL)
        self.serial = Channel(self.runtime_dir, SERIAL_CHANNEL)
        self.monitor_path = self.monitor.socket_path
        self.monitor_endpoint = Endpoint(path=str(self.monitor.socket_path))
        self.serial_endpoint = Endpoint(path=str(self.serial.socket_path))
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
        return pid if procctl.is_qemu_process(pid) else None

    def is_running(self):
        return self.pid() is not None

    def start(self, display=None, resume=None, accel_override=None):
        """Boot the VM detached. ``resume`` restores a snapshot tag first.

        ``accel_override`` replaces the configured accelerator for this run only.
        """
        if self.is_running():
            raise VmError(f"VM {self.name} is already running (pid {self.pid()})")
        chosen_accel = accel_override or self.config.accel
        require_accel(chosen_accel)
        self._prepare_runtime_dir()
        if not self.store.disk_path(self.name).exists():
            self.store.reset_disk(self.name)
        display = resolve_display(display or self.config.display, paths.qemu_binary())
        command = self._qemu_command(display, resume, chosen_accel)
        with open(self.qemu_log_path, "wb") as qemu_log:
            process = subprocess.Popen(
                command, env=self._qemu_env(), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=qemu_log,
                **procctl.detach_options(headless=display == "none"))
        self.pid_path.write_text(f"{process.pid}\n")
        self._wait_for_monitor(process)
        return process.pid

    def wait_desktop(self, timeout=BOOT_TIMEOUT_S):
        """Block until the guest reports its desktop is up; returns boot ms."""
        self.wait_serial(DESKTOP_READY_MARKER, timeout)
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
        if not self._wait_exit(POWEROFF_GRACE_S):
            procctl.force_kill(pid)
            self._wait_exit(POWEROFF_GRACE_S)
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
        with self.monitor.locate().connect() as sock:
            sock.settimeout(HMP_TIMEOUT_S)
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
        self._wait_file_written(path)
        return path

    def _wait_file_written(self, path):
        """QEMU may reply before the dump is flushed (Windows): wait for a stable size."""
        deadline = time.monotonic() + SCREENSHOT_WRITE_TIMEOUT_S
        last_size = -1
        while time.monotonic() < deadline:
            size = path.stat().st_size if path.exists() else 0
            if size > 0 and size == last_size:
                return
            last_size = size
            time.sleep(POLL_INTERVAL_S)
        raise VmError(f"screenshot was not written to {path}")

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
        self.serial.locate().send(text.encode())

    def shell(self, command, timeout=60.0):
        """Run a guest shell command over serial; return what it printed.

        Completion is detected by the shell prompt: the guest prints its
        prompt before reading a command and reprints it once the command
        finishes, so the prompt count must grow by one.
        """
        offset = len(self.serial_bytes())
        prompts_before = len(SHELL_PROMPT_PATTERN.findall(self.serial_bytes()))
        self.type_serial(command + "\n")
        deadline = time.monotonic() + timeout
        encoded = command.encode()
        while time.monotonic() < deadline:
            output = self.serial_bytes()[offset:]
            if len(SHELL_PROMPT_PATTERN.findall(self.serial_bytes())) > prompts_before:
                echoed = output.find(encoded)
                if echoed >= 0:
                    return _shell_output(output[echoed:], command)
                return ""
            time.sleep(POLL_INTERVAL_S)
        raise VmError(f"guest shell did not finish {command!r} in {timeout:.0f}s")

    def send_line(self, text):
        """Type one line on the serial console and return without waiting.

        Use this for foreground programs and REPLs that do not hand the
        console back to the shell prompt; poll the log with
        :meth:`wait_serial` for the evidence you expect.
        """
        self.type_serial(text + "\n")

    def wait_serial(self, marker, timeout, since_offset=0):
        """Block until ``marker`` appears on serial at/after ``since_offset``.

        Returns the offset just past the match. Raises :class:`VmError` on
        timeout or when the VM exits while waiting.
        """
        deadline = time.monotonic() + timeout
        while True:
            found = self.serial_bytes().find(marker, since_offset)
            if found >= 0:
                return found + len(marker)
            if not self.is_running():
                raise VmError(f"VM {self.name} exited while waiting for {marker!r}")
            if time.monotonic() >= deadline:
                break
            time.sleep(POLL_INTERVAL_S)
        raise VmError(f"timed out after {timeout:.0f}s waiting for {marker!r} on serial")

    # ── internals ────────────────────────────────────────────────────

    def _prepare_runtime_dir(self):
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.pid_path.unlink(missing_ok=True)
        self.serial_log_path.write_bytes(b"")
        self.monitor_endpoint = self.monitor.allocate()
        self.serial_endpoint = self.serial.allocate()

    def _qemu_command(self, display, resume, accel_choice=None):
        config = self.config
        disk = self.store.disk_path(self.name)
        command = [
            str(paths.qemu_binary()),
            "-M", f"{config.machine_type},graphics=on",
            "-m", f"{config.memory_gb}G",
            *accel.qemu_arguments(accel_choice or config.accel),
            "-kernel", str(config.kernel_path),
            "-dtb", str(paths.default_dtb()),
            "-drive", f"if=none,id=lobsterdisk,file={disk},format=qcow2",
            *self._channel_arguments(),
            "-display", display_argument(display),
        ]
        command += _device_arguments(config)
        if not config.audio or display == "none":
            command += ["-audio", "none"]
        if resume:
            command += ["-loadvm", resume]
        return command + list(config.extra_args)

    def _channel_arguments(self):
        log = _qemu_option_value(str(self.serial_log_path))
        return [
            "-chardev", f"socket,id=mon0,{self.monitor_endpoint.chardev_options()}",
            "-mon", "chardev=mon0,mode=readline",
            "-chardev", f"socket,id=ser0,{self.serial_endpoint.chardev_options()},"
                        f"logfile={log},logappend=on",
            "-serial", "chardev:ser0",
        ]

    def _qemu_env(self):
        env = dict(os.environ)
        for stale in LEGACY_DEVICE_ENV:
            env.pop(stale, None)
        return env

    def _wait_for_monitor(self, process):
        deadline = time.monotonic() + MONITOR_START_TIMEOUT_S
        while time.monotonic() < deadline:
            if process.poll() is not None:
                tail = self.qemu_log_path.read_text(errors="replace")[-LOG_TAIL_CHARS:]
                self.pid_path.unlink(missing_ok=True)
                raise VmError(f"QEMU exited during startup:\n{tail}")
            if self.monitor.is_ready():
                self.monitor.restrict()
                self.serial.restrict()
                return
            time.sleep(POLL_INTERVAL_S)
        log = self.qemu_log_path.read_text(errors="replace")[-LOG_TAIL_CHARS:]
        raise VmError(f"QEMU monitor never became reachable; qemu.log:\n{log}")

    def _wait_exit(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_running():
                self.pid_path.unlink(missing_ok=True)
                return True
            time.sleep(POLL_INTERVAL_S)
        return False


def require_accel(choice):
    """Raise a ``VmError`` that explains why ``choice`` cannot run on this host."""
    if choice != accel.KVM:
        return
    reason = accel.kvm_unavailable_reason()
    if reason:
        raise VmError(f"accel=kvm is not available: {reason}; "
                      f"{accel.SWITCH_BACK_HINT}, or start once with `--accel tcg`")


def _qemu_option_value(text):
    """QEMU option values treat a comma as a separator; a literal one is doubled."""
    return text.replace(",", ",,")


def _device_arguments(config):
    width, height = config.resolution.split("x")
    arguments = [
        "-global", f"lobster-sys.width={width}",
        "-global", f"lobster-sys.height={height}",
    ]
    if config.shared_folder:
        folder = _qemu_option_value(config.shared_folder)
        arguments += ["-global", f"lobster-share.dir={folder}"]
    return arguments


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
