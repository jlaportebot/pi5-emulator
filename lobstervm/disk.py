"""Disk tools: inspect, export, import and grow a VM's disk (E2.7).

A VM's disk is ``<vm dir>/disk.qcow2``. Out of the box it is an overlay on the
pristine base image; ``import`` and ``resize`` replace it with a standalone
qcow2 (the overlay and its snapshots are flattened away).

The guest's home-grown ext2 driver (``src/fs/ext2.rs`` in LobsterOS) reads the
whole group descriptor table into a fixed buffer of ``MAX_BLOCK_GROUPS``
entries, so a volume with more block groups cannot be mounted. That bounds the
volume at ``MAX_BLOCK_GROUPS * blocks_per_group * block_size`` bytes.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .config import VmError, run_qemu_img
from .machine import Machine

KIB = 1 << 10
MIB = 1 << 20
SIZE_UNITS = {"": 1, "K": KIB, "M": MIB, "G": 1 << 30, "T": 1 << 40}
SIZE_PATTERN = re.compile(r"^(\d+)\s*([KMGT]?)(?:I?B)?$", re.IGNORECASE)

MAX_BLOCK_GROUPS = 128
MAX_BLOCKS_PER_GROUP = 8192
MAX_BLOCK_BYTES = 1024
MAX_VOLUME_BYTES = MAX_BLOCK_GROUPS * MAX_BLOCKS_PER_GROUP * MAX_BLOCK_BYTES

EXT2_MAGIC = 0xEF53
SUPERBLOCK_OFFSET = 1024
SUPERBLOCK_READ_BYTES = 128
BLOCKS_COUNT_OFFSET = 4
FIRST_DATA_BLOCK_OFFSET = 20
LOG_BLOCK_SIZE_OFFSET = 24
BLOCKS_PER_GROUP_OFFSET = 32
MAGIC_OFFSET = 56
MIN_BLOCK_SIZE = 1024

FSCK_OK_CODES = (0, 1)
DISK_FILE = "disk.qcow2"
WORK_RAW_FILE = "disk.work.raw"
NEW_DISK_FILE = "disk.qcow2.new"
EXPORT_FORMATS = ("raw", "qcow2")
IMPORT_FORMATS = ("raw", "qcow2")


def parse_size(text):
    """Parse ``128M`` / ``1G`` / ``1048576`` (binary units) into bytes."""
    match = SIZE_PATTERN.match(str(text).strip())
    if match is None:
        raise VmError(f"invalid size {text!r} (examples: 128M, 1G, 1048576)")
    size = int(match.group(1)) * SIZE_UNITS[match.group(2).upper()]
    if size <= 0:
        raise VmError(f"size must be positive, got {text!r}")
    return size


def human_size(size):
    """Format a byte count for people (``64.0 MiB``)."""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < KIB or unit == "GiB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= KIB
    return f"{size} B"


def validate_new_size(new_size, current_size):
    """Reject sizes the resize cannot honour, with the reason."""
    if new_size <= current_size:
        raise VmError(f"new size {human_size(new_size)} must be larger than the "
                      f"current {human_size(current_size)} (shrinking is not supported)")
    if new_size % MIB:
        raise VmError(f"new size {new_size} is not a multiple of 1 MiB")
    if new_size > MAX_VOLUME_BYTES:
        raise VmError(
            f"new size {human_size(new_size)} exceeds the guest ext2 driver limit "
            f"of {MAX_BLOCK_GROUPS} block groups "
            f"({human_size(MAX_VOLUME_BYTES)} with 1 KiB blocks)")


def image_info(path):
    """``qemu-img info`` of ``path`` as a dict (safe while a VM uses it)."""
    output = run_qemu_img(["info", "--force-share", "--output=json", str(path)])
    return json.loads(output)


def describe(store, name):
    """Lines describing the VM's disk (works while the VM runs)."""
    path = store.disk_path(name)
    if not path.is_file():
        raise VmError(f"VM {name} has no disk at {path}")
    info = image_info(path)
    virtual = info["virtual-size"]
    backing = info.get("full-backing-filename") or info.get("backing-filename")
    tags = [snap["name"] for snap in info.get("snapshots", [])]
    return [
        f"disk         {path}",
        f"format       {info['format']}",
        f"virtual size {virtual} bytes ({human_size(virtual)})",
        f"actual size  {info.get('actual-size', 0)} bytes "
        f"({human_size(info.get('actual-size', 0))})",
        f"backing file {backing or 'none (standalone)'}",
        f"snapshots    {', '.join(tags) if tags else 'none'}",
    ]


def require_ext2(raw_path):
    """Raise unless ``raw_path`` holds an ext2 volume the guest can mount."""
    with open(raw_path, "rb") as handle:
        handle.seek(SUPERBLOCK_OFFSET)
        superblock = handle.read(SUPERBLOCK_READ_BYTES)
    if len(superblock) < SUPERBLOCK_READ_BYTES or _u16(superblock, MAGIC_OFFSET) != EXT2_MAGIC:
        raise VmError(f"{raw_path} does not hold an ext2 filesystem at offset 0 "
                      f"(no 0x{EXT2_MAGIC:04X} magic in the superblock)")
    _require_guest_geometry(raw_path, superblock)


def _require_guest_geometry(raw_path, superblock):
    blocks = _u32(superblock, BLOCKS_COUNT_OFFSET)
    per_group = _u32(superblock, BLOCKS_PER_GROUP_OFFSET)
    first_block = _u32(superblock, FIRST_DATA_BLOCK_OFFSET)
    if per_group == 0:
        raise VmError(f"{raw_path} has a corrupt ext2 superblock (0 blocks per group)")
    groups = (blocks - first_block + per_group - 1) // per_group
    if groups > MAX_BLOCK_GROUPS:
        raise VmError(f"{raw_path} has {groups} block groups; the guest ext2 driver "
                      f"supports at most {MAX_BLOCK_GROUPS} block groups")


def _u16(data, offset):
    return int.from_bytes(data[offset:offset + 2], "little")


def _u32(data, offset):
    return int.from_bytes(data[offset:offset + 4], "little")


def _require_stopped(store, name):
    if Machine(name, store).is_running():
        raise VmError(f"VM {name} is running; stop it first")


def _require_yes(yes, action, consequence):
    if not yes:
        raise VmError(f"disk {action} {consequence}; pass --yes to confirm")


def _convert(source, target, source_format, target_format):
    run_qemu_img(["convert", "-f", source_format, "-O", target_format,
                  str(source), str(target)])


def _discard(*paths):
    for path in paths:
        Path(path).unlink(missing_ok=True)


def export_disk(store, name, output, fmt="raw", force=False):
    """Write a flattened, standalone copy of the VM's disk to ``output``."""
    if fmt not in EXPORT_FORMATS:
        raise VmError(f"export format must be one of {EXPORT_FORMATS}, got {fmt!r}")
    output = Path(output)
    _require_stopped(store, name)
    if output.exists() and not force:
        raise VmError(f"{output} already exists; use --force to overwrite")
    partial = output.with_name(output.name + ".part")
    try:
        _convert(store.disk_path(name), partial, "qcow2", fmt)
        os.replace(partial, output)
    finally:
        _discard(partial)
    return output


def import_disk(store, name, image, yes=False):
    """Replace the VM's disk with a standalone qcow2 made from ``image``."""
    image = Path(image)
    _require_yes(yes, "import", "replaces the VM's disk; existing data and snapshots are lost")
    _require_stopped(store, name)
    if not image.is_file():
        raise VmError(f"image not found: {image}")
    source_format = image_info(image)["format"]
    if source_format not in IMPORT_FORMATS:
        raise VmError(f"unsupported image format {source_format!r} for {image} "
                      f"(need one of {IMPORT_FORMATS})")
    vm_dir = store.vm_dir(name)
    work, staged = vm_dir / WORK_RAW_FILE, vm_dir / NEW_DISK_FILE
    try:
        _check_image_is_ext2(image, source_format, work)
        _convert(image, staged, source_format, "qcow2")
        os.replace(staged, store.disk_path(name))
    finally:
        _discard(work, staged)


def _check_image_is_ext2(image, source_format, work):
    if source_format == "raw":
        require_ext2(image)
        return
    _convert(image, work, source_format, "raw")
    require_ext2(work)


def resize_disk(store, name, size_text, yes=False):
    """Grow the guest volume to ``size_text``; snapshots are discarded."""
    _require_yes(yes, "resize", "flattens the disk; existing snapshots are lost")
    _require_stopped(store, name)
    new_size = parse_size(size_text)
    current = image_info(store.disk_path(name))["virtual-size"]
    validate_new_size(new_size, current)
    vm_dir = store.vm_dir(name)
    work, staged = vm_dir / WORK_RAW_FILE, vm_dir / NEW_DISK_FILE
    try:
        _convert(store.disk_path(name), work, "qcow2", "raw")
        _grow_filesystem(work, new_size)
        _convert(work, staged, "raw", "qcow2")
        os.replace(staged, store.disk_path(name))
    finally:
        _discard(work, staged)
    return new_size


def _grow_filesystem(raw_path, new_size):
    _run_e2fs("e2fsck", ["-fy", str(raw_path)], FSCK_OK_CODES)
    run_qemu_img(["resize", "-f", "raw", str(raw_path), str(new_size)])
    _run_e2fs("resize2fs", [str(raw_path)], (0,))
    _run_e2fs("e2fsck", ["-fn", str(raw_path)], (0,))


def _run_e2fs(tool, args, ok_codes):
    binary = shutil.which(tool)
    if binary is None:
        raise VmError(f"{tool} is not installed (apt install e2fsprogs)")
    result = subprocess.run([binary, *args], capture_output=True, text=True)
    if result.returncode not in ok_codes:
        detail = (result.stdout + result.stderr).strip()
        raise VmError(f"{tool} failed on {args[-1]} (exit {result.returncode}): {detail}")
