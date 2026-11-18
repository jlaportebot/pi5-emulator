"""Process control that works on POSIX and Windows.

Detaching a child from the launching terminal, asking whether a pid is still
a QEMU process, and killing it differ per OS; everything else in lobstervm
goes through this module.
"""
import ctypes
import os
import signal
import subprocess
from pathlib import Path

from .paths import WINDOWS, host_os

CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259
FORCED_EXIT_CODE = 1
IMAGE_NAME_CHARS = 1024
QEMU_IMAGE_PREFIX = "qemu-system"
PS_TIMEOUT_S = 5


def detach_options(headless):
    """``subprocess.Popen`` keyword arguments that detach the child."""
    if host_os() != WINDOWS:
        return {"start_new_session": True}
    flags = CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
    if headless:
        flags |= CREATE_NO_WINDOW
    return {"creationflags": flags}


def _kernel32():
    return ctypes.windll.kernel32


def _windows_exit_code(pid):
    """Exit code of ``pid`` (``STILL_ACTIVE`` while running), or None."""
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return None
        return code.value
    finally:
        kernel32.CloseHandle(handle)


def is_alive(pid):
    """True while a process with this pid exists (and is not a zombie shell)."""
    if host_os() == WINDOWS:
        return _windows_exit_code(pid) == STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def force_kill(pid):
    """Terminate ``pid`` immediately (SIGKILL / TerminateProcess)."""
    if host_os() != WINDOWS:
        os.kill(pid, signal.SIGKILL)
        return
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return
    try:
        kernel32.TerminateProcess(handle, FORCED_EXIT_CODE)
    finally:
        kernel32.CloseHandle(handle)


def _linux_is_qemu(pid):
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return QEMU_IMAGE_PREFIX.encode() in cmdline and state != "Z"


def _ps_is_qemu(pid):
    try:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                                capture_output=True, text=True, timeout=PS_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return QEMU_IMAGE_PREFIX in result.stdout


def _windows_image_name(pid):
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(IMAGE_NAME_CHARS)
        size = ctypes.c_ulong(IMAGE_NAME_CHARS)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def is_qemu_process(pid):
    """Guard against pid reuse: the pid must still be a live QEMU process."""
    system = host_os()
    if system == WINDOWS:
        image = _windows_image_name(pid).replace("\\", "/").rsplit("/", 1)[-1]
        return image.lower().startswith(QEMU_IMAGE_PREFIX) and is_alive(pid)
    if system == "macos":
        return _ps_is_qemu(pid)
    return _linux_is_qemu(pid)
