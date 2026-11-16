#!/usr/bin/env python3
"""Build the LobsterOS base image (lobsteros.img) from the tracked file tree.

The image is a raw ext2 volume without a partition table. Its contents live
in ``base-root/`` and its ownership/modes in ``base-root.manifest`` (git keeps
neither empty directories nor owners), so the 64 MiB binary need not be
tracked.

    python3 tools/mkbase.py                       # build <repo>/lobsteros.img
    python3 tools/mkbase.py --output X --force    # build elsewhere / replace
    python3 tools/mkbase.py --check [--output X]  # prove X matches the tree
    python3 tools/mkbase.py --extract IMAGE       # (re)create tree + manifest

The geometry mirrors the original hand-made image and satisfies the guest's
ext2 driver (LobsterOS src/fs/ext2.rs): revision 1 with 256-byte inodes, 1 KiB
blocks (the driver reads the group descriptor table from block 2), at most
4096 inodes and 4096 blocks tracked per group (512-byte bitmap windows), at
most 128 block groups, and only the filetype incompat feature.
"""
import argparse
import hashlib
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lobstervm.config import VmError
from lobstervm.disk import MIB, parse_size, validate_new_size

TREE_DIR = REPO_ROOT / "base-root"
MANIFEST_FILE = REPO_ROOT / "base-root.manifest"
DEFAULT_IMAGE = REPO_ROOT / "lobsteros.img"

DEFAULT_SIZE = "64M"
BLOCK_SIZE = 1024
INODE_SIZE = 256
BYTES_PER_INODE = 4096
BLOCKS_PER_GROUP = 8192
RESERVED_BLOCKS_PERCENT = 5
VOLUME_LABEL = "LOBSTEROS"
VOLUME_UUID = "6a8fbc78-4ea3-41f5-b5d5-a9ceee142b1b"
HASH_SEED = "469c4a6f-03bb-4d9a-8d82-e95ea427b558"
FEATURES = ("ext_attr", "resize_inode", "dir_index", "filetype", "sparse_super",
            "large_file")
DEFAULT_MOUNT_OPTIONS = "user_xattr,acl"
LOST_FOUND = "lost+found"
PERMISSION_MASK = 0o7777
DEBUGFS_BANNER = "debugfs "

KIND_FILE, KIND_DIR, KIND_LINK = "f", "d", "l"
TYPE_BITS = {KIND_FILE: stat.S_IFREG, KIND_DIR: stat.S_IFDIR, KIND_LINK: stat.S_IFLNK}
KIND_BY_TYPE_BITS = {bits: kind for kind, bits in TYPE_BITS.items()}
SUPERBLOCK_FACTS = ("Filesystem volume name", "Filesystem UUID", "Filesystem features",
                    "Block size", "Inode size", "Inode count", "Block count",
                    "Default mount options")


@dataclass(frozen=True)
class Entry:
    """One filesystem object: kind, permission bits, owner and absolute path."""
    kind: str
    mode: int
    uid: int
    gid: int
    path: str


def format_manifest(entries):
    """Serialise entries, one ``kind mode uid gid path`` line each, sorted."""
    lines = [f"{e.kind} {e.mode:04o} {e.uid} {e.gid} {e.path}"
             for e in sorted(entries, key=lambda e: e.path)]
    return "\n".join(lines) + "\n"


def parse_manifest(text):
    """Parse :func:`format_manifest` output; blank lines are ignored."""
    entries = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip():
            entries.append(_parse_manifest_line(line, number))
    return entries


def _parse_manifest_line(line, number):
    parts = line.split(" ", 4)
    if len(parts) != 5 or parts[0] not in TYPE_BITS or not parts[4].startswith("/"):
        raise VmError(f"manifest line {number} is malformed: {line!r}")
    try:
        mode, uid, gid = int(parts[1], 8), int(parts[2]), int(parts[3])
    except ValueError:
        raise VmError(f"manifest line {number} has a bad number: {line!r}") from None
    return Entry(parts[0], mode, uid, gid, parts[4])


def _tool(name):
    binary = shutil.which(name)
    if binary is None:
        raise VmError(f"{name} is not installed (apt install e2fsprogs)")
    return binary


def _run(tool, args, ok_codes=(0,), data=False):
    result = subprocess.run([_tool(tool), *args], capture_output=True, text=not data)
    if result.returncode not in ok_codes:
        detail = result.stderr if not data else result.stderr.decode(errors="replace")
        raise VmError(f"{tool} {' '.join(map(str, args))} failed "
                      f"(exit {result.returncode}): {detail.strip()}")
    return result


def _debugfs(image, request, write=False):
    args = ["-w"] if write else []
    return _run("debugfs", [*args, "-R", request, str(image)], data=True)


def _quote(path):
    return '"' + path.replace('"', '\\"') + '"'


def read_image_tree(image):
    """Map path -> attributes of every object in ``image`` (no lost+found)."""
    tree = {}
    pending = ["/"]
    while pending:
        directory = pending.pop()
        for name, attrs in _list_directory(image, directory):
            path = directory.rstrip("/") + "/" + name
            tree[path] = _describe(image, path, attrs)
            if attrs["kind"] == KIND_DIR:
                pending.append(path)
    tree["/"] = _describe(image, "/", _root_attrs(image))
    return tree


def _list_directory(image, directory):
    for name, attrs in _read_listing(image, directory):
        if name in (".", "..") or (directory == "/" and name == LOST_FOUND):
            continue
        yield name, attrs


def _read_listing(image, directory):
    listing = _debugfs(image, f"ls -lp {_quote(directory)}").stdout.decode()
    for line in listing.splitlines():
        fields = line.split("/")
        if len(fields) >= 8:
            yield fields[5], _entry_attrs(fields[2], fields[3], fields[4], fields[6])


def _entry_attrs(mode_text, uid, gid, size):
    mode = int(mode_text, 8)
    kind = KIND_BY_TYPE_BITS.get(stat.S_IFMT(mode))
    if kind is None:
        raise VmError(f"unsupported file type in image (mode {mode_text})")
    return {"kind": kind, "mode": mode & PERMISSION_MASK, "uid": int(uid),
            "gid": int(gid), "size": int(size) if size else 0}


def _root_attrs(image):
    for name, attrs in _read_listing(image, "/"):
        if name == ".":
            return attrs
    raise VmError(f"cannot read the root directory of {image}")


def _describe(image, path, attrs):
    attrs = dict(attrs)
    if attrs["kind"] == KIND_FILE:
        attrs["sha256"] = hashlib.sha256(read_file(image, path)).hexdigest()
    elif attrs["kind"] == KIND_LINK:
        attrs["target"] = _link_target(image, path)
    return attrs


def read_file(image, path):
    """Bytes of a regular file inside the image."""
    return _debugfs(image, f"cat {_quote(path)}").stdout


def _link_target(image, path):
    for line in _debugfs(image, f"stat {_quote(path)}").stdout.decode().splitlines():
        if line.startswith("Fast link dest:"):
            return line.split('"')[1]
    return ""


def superblock_facts(image):
    """The geometry facts that must match between two images."""
    text = _run("dumpe2fs", ["-h", str(image)]).stdout
    facts = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key in SUPERBLOCK_FACTS:
            facts[key] = " ".join(value.split())
    return facts


def compare_images(built, reference):
    """Differences between two images as human-readable lines (empty = equal)."""
    problems = []
    facts_a, facts_b = superblock_facts(built), superblock_facts(reference)
    for key in SUPERBLOCK_FACTS:
        if facts_a.get(key) != facts_b.get(key):
            problems.append(f"{key}: built={facts_a.get(key)!r} "
                            f"reference={facts_b.get(key)!r}")
    tree_a, tree_b = read_image_tree(built), read_image_tree(reference)
    for path in sorted(set(tree_a) | set(tree_b)):
        if tree_a.get(path) != tree_b.get(path):
            problems.append(f"{path}: built={tree_a.get(path)} "
                            f"reference={tree_b.get(path)}")
    return problems


def tree_entries(image):
    """Manifest entries describing ``image``."""
    return [Entry(a["kind"], a["mode"], a["uid"], a["gid"], path)
            for path, a in read_image_tree(image).items()]


def extract_image(image, tree_dir, manifest_file):
    """Write ``image``'s contents to ``tree_dir`` and its metadata to the manifest."""
    entries = tree_entries(image)
    for entry in sorted(entries, key=lambda e: e.path):
        target = tree_dir / entry.path.lstrip("/")
        if entry.kind == KIND_DIR:
            target.mkdir(parents=True, exist_ok=True)
        elif entry.kind == KIND_FILE:
            target.write_bytes(read_file(image, entry.path))
        else:
            target.symlink_to(_link_target(image, entry.path))
    manifest_file.write_text(format_manifest(entries))
    return entries


def stage_tree(tree_dir, entries, stage):
    """Copy the tracked tree to ``stage`` and create directories git dropped."""
    shutil.copytree(tree_dir, stage, symlinks=True, dirs_exist_ok=True)
    for entry in entries:
        target = stage / entry.path.lstrip("/")
        if entry.kind == KIND_DIR:
            target.mkdir(parents=True, exist_ok=True)
        elif not (target.exists() or target.is_symlink()):
            raise VmError(f"manifest lists {entry.path} but {tree_dir} lacks it")
    extra = {p.relative_to(stage).as_posix() for p in stage.rglob("*")} \
        - {e.path.lstrip("/") for e in entries}
    if extra:
        raise VmError(f"{tree_dir} has files missing from the manifest: {sorted(extra)}")


def mke2fs_args(output, size_bytes, stage):
    """The mke2fs command line reproducing the original image geometry."""
    return ["-q", "-F", "-t", "ext2", "-o", "Linux", "-r", "1",
            "-b", str(BLOCK_SIZE), "-I", str(INODE_SIZE), "-i", str(BYTES_PER_INODE),
            "-g", str(BLOCKS_PER_GROUP), "-m", str(RESERVED_BLOCKS_PERCENT),
            "-L", VOLUME_LABEL, "-U", VOLUME_UUID,
            "-O", "none," + ",".join(FEATURES),
            "-E", f"root_owner=0:0,hash_seed={HASH_SEED}",
            "-d", str(stage), str(output), str(size_bytes // BLOCK_SIZE)]


def apply_ownership(image, entries):
    """Set mode/uid/gid from the manifest (mke2fs -d copies the caller's)."""
    with tempfile.NamedTemporaryFile("w", suffix=".cmd", delete=False) as script:
        for entry in entries:
            mode = TYPE_BITS[entry.kind] | entry.mode
            quoted = _quote(entry.path)
            script.write(f"sif {quoted} mode 0{mode:o}\n"
                         f"sif {quoted} uid {entry.uid}\n"
                         f"sif {quoted} gid {entry.gid}\n")
    try:
        result = _run("debugfs", ["-w", "-f", script.name, str(image)])
    finally:
        Path(script.name).unlink(missing_ok=True)
    problems = [line for line in result.stderr.splitlines()
                if line.strip() and not line.startswith(DEBUGFS_BANNER)]
    if problems:
        raise VmError(f"debugfs could not apply the manifest: {problems[0]}")


def build_image(tree_dir, manifest_file, output, size_bytes):
    """Build ``output`` from the tree and manifest; fsck must come back clean."""
    validate_new_size(size_bytes, 0)
    entries = parse_manifest(Path(manifest_file).read_text())
    with tempfile.TemporaryDirectory() as scratch:
        stage = Path(scratch) / "root"
        stage_tree(Path(tree_dir), entries, stage)
        output = Path(output)
        output.unlink(missing_ok=True)
        try:
            _run("mke2fs", mke2fs_args(output, size_bytes, stage))
            _run("tune2fs", ["-o", DEFAULT_MOUNT_OPTIONS, str(output)])
            apply_ownership(output, entries)
            _run("e2fsck", ["-fn", str(output)])
        except VmError:
            output.unlink(missing_ok=True)
            raise
    return output


def check_image(tree_dir, manifest_file, reference, size_bytes):
    """Build to a temp file and diff against ``reference``; returns problems."""
    if not Path(reference).is_file():
        raise VmError(f"nothing to compare against: {reference} does not exist")
    with tempfile.TemporaryDirectory() as scratch:
        built = build_image(tree_dir, manifest_file, Path(scratch) / "built.img",
                            size_bytes)
        return compare_images(built, reference)


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Build lobsteros.img from base-root/ and base-root.manifest")
    parser.add_argument("--output", type=Path, default=DEFAULT_IMAGE,
                        help="image to write (or to compare with, for --check)")
    parser.add_argument("--size", default=DEFAULT_SIZE, help="volume size (default 64M)")
    parser.add_argument("--force", action="store_true", help="overwrite --output")
    parser.add_argument("--check", action="store_true",
                        help="build to a temp file and compare it with --output")
    parser.add_argument("--extract", type=Path, metavar="IMAGE",
                        help="write IMAGE's contents to base-root/ and the manifest")
    return parser.parse_args(argv)


def run(args):
    size = parse_size(args.size)
    if size % MIB:
        raise VmError(f"size {args.size} is not a multiple of 1 MiB")
    if args.extract:
        extract_image(args.extract, TREE_DIR, MANIFEST_FILE)
        print(f"Extracted {args.extract} into {TREE_DIR} and {MANIFEST_FILE}")
    elif args.check:
        problems = check_image(TREE_DIR, MANIFEST_FILE, args.output, size)
        print("\n".join(problems) if problems else
              f"OK: built image matches {args.output} (paths, types, modes, owners, "
              "sizes, content hashes, geometry, label, UUID)")
        return 1 if problems else 0
    else:
        if args.output.exists() and not args.force:
            raise VmError(f"{args.output} already exists; use --force to overwrite")
        build_image(TREE_DIR, MANIFEST_FILE, args.output, size)
        print(f"Built {args.output} ({size} bytes, fsck clean)")
    return 0


def main(argv=None):
    try:
        return run(parse_args(argv))
    except VmError as error:
        print(f"mkbase: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
