"""Locate the emulator's moving parts without hard-coded absolute paths.

Every location can be overridden with an environment variable. Without an
override the component is looked up in the installed bundle (see
``packaging/LAYOUT.md``) and then in this repository checkout. VM data and
runtime directories follow each operating system's conventions.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
QEMU_BASENAME = "qemu-system-aarch64"
QEMU_IMG_BASENAME = "qemu-img"
KERNEL_FILE = "kernel8.img"
DTB_FILE = "bcm2712-rpi-5-b.dtb"
BASE_IMAGE_FILE = "lobsteros.img"
BUNDLE_QEMU_DIR = Path("qemu") / "bin"
BUNDLE_SHARE_DIR = Path("share") / "lobsteros"
MACOS_APP_EXE_DIR = "MacOS"
MACOS_APP_RESOURCES_DIR = "Resources"
APP_DIR_NAME = "LobsterVM"
LINUX_DIR_NAME = "lobstervm"
WINDOWS_RUN_DIR = "run"
MACOS_SUPPORT_PARTS = ("Library", "Application Support", APP_DIR_NAME)
WINDOWS_FALLBACK_PARTS = ("AppData", "Local")

WINDOWS = "windows"
MACOS = "macos"
LINUX = "linux"


class MissingComponent(RuntimeError):
    """A required file (QEMU binary, kernel, DTB, disk) could not be found."""


def host_os():
    """``windows``, ``macos`` or ``linux`` (any other POSIX counts as Linux)."""
    if sys.platform.startswith("win"):
        return WINDOWS
    if sys.platform == "darwin":
        return MACOS
    return LINUX


def exe_name(basename):
    """``basename`` with the platform's executable suffix."""
    return basename + (".exe" if host_os() == WINDOWS else "")


def bundle_dirs():
    """Directories that may hold the installed ``qemu/`` and ``share/`` trees.

    Empty unless running from a frozen (PyInstaller) build.
    """
    if not getattr(sys, "frozen", False):
        return []
    exe_dir = Path(sys.executable).resolve().parent
    dirs = [exe_dir]
    if exe_dir.name == MACOS_APP_EXE_DIR:
        dirs.append(exe_dir.parent / MACOS_APP_RESOURCES_DIR)
    dirs.append(exe_dir.parent)
    return dirs


def is_bundled(path):
    """True when ``path`` lives inside the installed bundle."""
    resolved = Path(path).resolve()
    return any(root in resolved.parents for root in bundle_dirs())


def _bundle_paths(relative):
    return [root / relative for root in bundle_dirs()]


def _resolve(label, hint, variable, candidates):
    """The override in ``variable`` when set, else the first existing candidate.

    A set override is authoritative: it is the only location tried, so a typo
    or wrong working directory fails loudly instead of booting a stale file.
    """
    override = os.environ.get(variable)
    if override:
        return _checked_override(label, hint, variable, override)
    looked = [Path(c) for c in candidates if c]
    for candidate in looked:
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(c) for c in looked) or "(no candidate locations)"
    raise MissingComponent(f"{label} not found; {hint}. Looked in: {searched}")


def _checked_override(label, hint, variable, value):
    path = Path(value)
    if path.is_file():
        return path
    raise MissingComponent(
        f"{label} not found: {variable}={value!r} is not an existing file "
        f"(it resolved to {path.absolute()}; relative values resolve against "
        f"the current directory {Path.cwd()}); {hint}")


def qemu_binary():
    """The LobsterOS QEMU fork (it must provide the raspi5b machine)."""
    return _resolve("QEMU fork", "set LOBSTER_QEMU to qemu-system-aarch64 built "
                    "from jlaportebot/lobster-qemu", "LOBSTER_QEMU", [
        *_bundle_paths(BUNDLE_QEMU_DIR / exe_name(QEMU_BASENAME)),
        REPO_ROOT.parent / "qemu-src" / "build-opt" / QEMU_BASENAME,
        Path.home() / "qemu-src" / "build-opt" / QEMU_BASENAME,
        _which(f"{QEMU_BASENAME}-lobster"),
    ])


def qemu_img_binary():
    """``qemu-img`` matching the QEMU fork (bundle, QEMU's own dir, then PATH)."""
    name = exe_name(QEMU_IMG_BASENAME)
    return _resolve("qemu-img", "set LOBSTER_QEMU_IMG or install qemu-utils",
                    "LOBSTER_QEMU_IMG", [
        *_bundle_paths(BUNDLE_QEMU_DIR / name),
        *_next_to_qemu(name),
        _which(QEMU_IMG_BASENAME),
    ])


def _next_to_qemu(name):
    try:
        return [qemu_binary().parent / name]
    except MissingComponent:
        return []


def _which(program):
    return shutil.which(program)


def default_kernel():
    return _resolve("kernel8.img", f"set LOBSTER_KERNEL or copy a kernel into "
                    f"{REPO_ROOT}", "LOBSTER_KERNEL", [
        *_bundle_paths(BUNDLE_SHARE_DIR / KERNEL_FILE),
        REPO_ROOT / KERNEL_FILE,
    ])


def default_dtb():
    return _resolve(DTB_FILE, "set LOBSTER_DTB", "LOBSTER_DTB", [
        *_bundle_paths(BUNDLE_SHARE_DIR / DTB_FILE),
        REPO_ROOT / DTB_FILE,
    ])


def base_disk():
    """The pristine ext2 system image every VM's disk is layered on."""
    return _resolve(BASE_IMAGE_FILE, "build it with `python3 tools/mkbase.py` "
                    "or set LOBSTER_BASE_DISK", "LOBSTER_BASE_DISK", [
        *_bundle_paths(BUNDLE_SHARE_DIR / BASE_IMAGE_FILE),
        REPO_ROOT / BASE_IMAGE_FILE,
    ])


def missing_components():
    """Messages for every required component that cannot be found."""
    problems = []
    for finder in (qemu_binary, qemu_img_binary, default_kernel, default_dtb,
                   base_disk):
        try:
            finder()
        except MissingComponent as error:
            problems.append(str(error))
    return problems


def _windows_local_appdata():
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) if local else Path.home().joinpath(*WINDOWS_FALLBACK_PARTS)


def data_home():
    """Where VM definitions and disks live."""
    root = os.environ.get("LOBSTERVM_HOME")
    if root:
        return Path(root)
    system = host_os()
    if system == WINDOWS:
        return _windows_local_appdata() / APP_DIR_NAME
    if system == MACOS:
        return Path.home().joinpath(*MACOS_SUPPORT_PARTS)
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / LINUX_DIR_NAME


def runtime_home():
    """Where sockets, port files and pid files of running VMs live.

    Kept short on purpose: macOS limits Unix socket paths to 104 bytes.
    """
    root = os.environ.get("LOBSTERVM_RUNTIME")
    if root:
        return Path(root)
    system = host_os()
    if system == WINDOWS:
        return _windows_local_appdata() / APP_DIR_NAME / WINDOWS_RUN_DIR
    if system == MACOS:
        return Path(tempfile.gettempdir()) / f"lobstervm-{os.getuid()}"
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / LINUX_DIR_NAME
    return Path(tempfile.gettempdir()) / f"lobstervm-{os.getuid()}"
