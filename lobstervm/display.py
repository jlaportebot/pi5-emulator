"""Pick the QEMU display backend for ``auto`` and format its options."""
import subprocess

from .paths import LINUX, MACOS, WINDOWS, host_os

GTK_OPTIONS = "gtk,show-tabs=off,show-cursor=off,grab-on-hover=on"
VNC_DISPLAY = "vnc=127.0.0.1:9"
PREFERRED_BY_OS = {MACOS: "cocoa", WINDOWS: "sdl", LINUX: "gtk"}
FALLBACK_ORDER = ("cocoa", "sdl", "gtk")
NO_DISPLAY = "none"
DISPLAY_HELP_TIMEOUT_S = 15
DISPLAY_HELP_HEADER = "available display"


def parse_display_help(text):
    """Backend names from the output of ``qemu-system-aarch64 -display help``."""
    names = []
    for line in text.splitlines():
        word = line.strip()
        if word and not word.lower().startswith(DISPLAY_HELP_HEADER):
            names.append(word.split()[0])
    return names


def available_displays(qemu):
    """Backends the given QEMU binary was built with (empty when unknown)."""
    try:
        result = subprocess.run([str(qemu), "-display", "help"], capture_output=True,
                                text=True, timeout=DISPLAY_HELP_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_display_help(result.stdout + result.stderr)


def pick_backend(available):
    """The preferred backend for this OS, else the first usable one."""
    preferred = PREFERRED_BY_OS[host_os()]
    if not available or preferred in available:
        return preferred
    for name in FALLBACK_ORDER:
        if name in available:
            return name
    return NO_DISPLAY


def resolve_display(display, qemu):
    """Turn ``auto`` into a concrete backend; pass everything else through."""
    if display != "auto":
        return display
    return pick_backend(available_displays(qemu))


def display_argument(display):
    """The ``-display`` option value for a concrete backend."""
    if display == "gtk":
        return GTK_OPTIONS
    if display == "vnc":
        return VNC_DISPLAY
    return display
