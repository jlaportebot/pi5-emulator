"""Persistent VM definitions (the equivalent of a VirtualBox .vbox file)."""
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import paths

VM_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
RESOLUTION_PATTERN = re.compile(r"^(\d{3,4})x(\d{3,4})$")
#: RAM size in GiB → QEMU machine type (each is a real Pi 5 board variant).
MACHINE_BY_MEMORY_GB = {2: "raspi5b-2g", 4: "raspi5b", 8: "raspi5b-8g", 16: "raspi5b-16g"}
DISPLAY_BACKENDS = ("gtk", "sdl", "vnc", "none")
CONFIG_FILE = "vm.json"
DISK_FILE = "disk.qcow2"


class VmError(RuntimeError):
    """A VM operation failed in a way the user can act on."""


@dataclass
class VmConfig:
    name: str
    memory_gb: int = 4
    resolution: str = "1920x1080"
    display: str = "gtk"
    audio: bool = True
    shared_folder: str = ""
    kernel: str = ""
    description: str = ""
    extra_args: list = field(default_factory=list)

    def validate(self):
        if not VM_NAME_PATTERN.match(self.name):
            raise VmError(f"invalid VM name {self.name!r} (letters, digits, _ . -)")
        if self.memory_gb not in MACHINE_BY_MEMORY_GB:
            raise VmError(f"memory must be one of {sorted(MACHINE_BY_MEMORY_GB)} GB")
        if not RESOLUTION_PATTERN.match(self.resolution):
            raise VmError(f"resolution must look like 1920x1080, got {self.resolution!r}")
        if self.display not in DISPLAY_BACKENDS:
            raise VmError(f"display must be one of {DISPLAY_BACKENDS}")
        if self.shared_folder and not Path(self.shared_folder).is_dir():
            raise VmError(f"shared folder is not a directory: {self.shared_folder}")
        if self.kernel and not Path(self.kernel).is_file():
            raise VmError(f"kernel image not found: {self.kernel}")

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
        self.reset_disk(config.name)
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
        run_qemu_img(["create", "-f", "qcow2", "-b", str(paths.base_disk()),
                      "-F", "raw", str(disk)])

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


def run_qemu_img(args):
    tool = shutil.which("qemu-img")
    if tool is None:
        raise VmError("qemu-img is not installed (apt install qemu-utils)")
    result = subprocess.run([tool, *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise VmError(f"qemu-img {args[0]} failed: {result.stderr.strip()}")
    return result.stdout
