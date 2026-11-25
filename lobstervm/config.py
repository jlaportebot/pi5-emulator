"""Persistent VM definitions (the equivalent of a VirtualBox .vbox file)."""
import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import accel, paths

VM_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
RESOLUTION_PATTERN = re.compile(r"^(\d{3,4})x(\d{3,4})$")
#: RAM size in GiB → QEMU machine type (each is a real Pi 5 board variant).
MACHINE_BY_MEMORY_GB = {2: "raspi5b-2g", 4: "raspi5b", 8: "raspi5b-8g", 16: "raspi5b-16g"}
DISPLAY_BACKENDS = ("auto", "gtk", "sdl", "cocoa", "vnc", "none")
DEFAULT_DISPLAY = "auto"
CONFIG_FILE = "vm.json"
DISK_FILE = "disk.qcow2"
BASE_DIR = "base"
BASE_HASH_CHARS = 16
HASH_CHUNK_BYTES = 1 << 20


class VmError(RuntimeError):
    """A VM operation failed in a way the user can act on."""


@dataclass
class VmConfig:
    name: str
    memory_gb: int = 4
    resolution: str = "1920x1080"
    display: str = DEFAULT_DISPLAY
    audio: bool = True
    shared_folder: str = ""
    #: Directory holding ``include/`` and ``liblobsterc.a`` for the guest's
    #: ``cc``; empty keeps the guest's cc off (with it on the guest's
    #: compiler runs on the host and can therefore read host files).
    host_compile_libc: str = ""
    kernel: str = ""
    description: str = ""
    extra_args: list = field(default_factory=list)
    accel: str = accel.DEFAULT_ACCEL

    def validate(self):
        if not VM_NAME_PATTERN.match(self.name):
            raise VmError(f"invalid VM name {self.name!r} (letters, digits, _ . -)")
        if self.name == BASE_DIR:
            raise VmError(f"{BASE_DIR!r} is reserved for the managed base image")
        if self.memory_gb not in MACHINE_BY_MEMORY_GB:
            raise VmError(f"memory must be one of {sorted(MACHINE_BY_MEMORY_GB)} GB")
        if not RESOLUTION_PATTERN.match(self.resolution):
            raise VmError(f"resolution must look like 1920x1080, got {self.resolution!r}")
        if self.display not in DISPLAY_BACKENDS:
            raise VmError(f"display must be one of {DISPLAY_BACKENDS}")
        if self.accel not in accel.ACCELERATORS:
            raise VmError(f"accel must be one of {accel.ACCELERATORS}")
        if self.shared_folder and not Path(self.shared_folder).is_dir():
            raise VmError(f"shared folder is not a directory: {self.shared_folder}")
        self._validate_host_compile_libc()
        if self.kernel and not Path(self.kernel).is_file():
            raise VmError(f"kernel image not found: {self.kernel}")

    def _validate_host_compile_libc(self):
        """The host compiler needs a directory with ``include/`` and ``liblobsterc.a``."""
        if not self.host_compile_libc:
            return
        libc = Path(self.host_compile_libc)
        has_headers = (libc / "include").is_dir()
        has_archive = (libc / "liblobsterc.a").is_file()
        if not libc.is_dir() or not has_headers or not has_archive:
            raise VmError("host compiler libc dir must contain include/ "
                          f"and liblobsterc.a: {self.host_compile_libc}")

    @property
    def machine_type(self):
        return MACHINE_BY_MEMORY_GB[self.memory_gb]

    @property
    def kernel_path(self):
        return Path(self.kernel) if self.kernel else paths.default_kernel()


class VmStore:
    """Directory of VMs: ``<data_home>/<name>/{vm.json,disk.qcow2}``."""

    def __init__(self, root=None):
        self.root = Path(root) if root else paths.data_home()

    def vm_dir(self, name):
        return self.root / name

    def disk_path(self, name):
        return self.vm_dir(name) / DISK_FILE

    def exists(self, name):
        return (self.vm_dir(name) / CONFIG_FILE).is_file()

    def list_names(self):
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if (p / CONFIG_FILE).is_file())

    def load(self, name):
        config_path = self.vm_dir(name) / CONFIG_FILE
        if not config_path.is_file():
            raise VmError(f"no such VM: {name}")
        raw = json.loads(config_path.read_text())
        known = {f.name for f in fields(VmConfig)}
        return VmConfig(**{k: v for k, v in raw.items() if k in known})

    def save(self, config):
        config.validate()
        self.vm_dir(config.name).mkdir(parents=True, exist_ok=True)
        config_path = self.vm_dir(config.name) / CONFIG_FILE
        config_path.write_text(json.dumps(asdict(config), indent=2) + "\n")

    def create(self, config):
        if self.exists(config.name):
            raise VmError(f"VM already exists: {config.name}")
        self.save(config)
        try:
            self.reset_disk(config.name)
        except (VmError, paths.MissingComponent):
            shutil.rmtree(self.vm_dir(config.name), ignore_errors=True)
            raise
        return config

    def delete(self, name):
        if not self.exists(name):
            raise VmError(f"no such VM: {name}")
        shutil.rmtree(self.vm_dir(name))

    def reset_disk(self, name):
        """(Re)create the VM's disk as a fresh overlay on the base image.

        The base image is never written; every guest write and every
        snapshot lands in the per-VM qcow2 overlay.
        """
        disk = self.disk_path(name)
        if disk.exists():
            disk.unlink()
        run_qemu_img(["create", "-f", "qcow2", "-b", str(self.backing_image()),
                      "-F", "raw", str(disk)])

    def backing_image(self):
        """The base image overlays are layered on.

        An image from the installed bundle is read-only and moves on update,
        so it is copied once into ``<root>/base/lobsteros-<sha256>.img``.
        """
        source = paths.base_disk()
        if not paths.is_bundled(source):
            return source
        target_dir = self.root / BASE_DIR
        target = target_dir / f"lobsteros-{_file_digest(source)}.img"
        if not target.is_file():
            target_dir.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".part")
            shutil.copyfile(source, partial)
            os.replace(partial, target)
        return target

    def snapshots(self, name):
        """Snapshot tags stored in the VM's disk (VM must be stopped)."""
        output = run_qemu_img(["snapshot", "-l", str(self.disk_path(name))])
        tags = []
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].isdigit():
                tags.append(parts[1])
        return tags

    def delete_snapshot(self, name, tag):
        run_qemu_img(["snapshot", "-d", tag, str(self.disk_path(name))])


def _file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()[:BASE_HASH_CHARS]


def run_qemu_img(args):
    try:
        tool = paths.qemu_img_binary()
    except paths.MissingComponent as error:
        raise VmError(str(error)) from error
    result = subprocess.run([str(tool), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise VmError(f"qemu-img {args[0]} failed: {result.stderr.strip()}")
    return result.stdout
