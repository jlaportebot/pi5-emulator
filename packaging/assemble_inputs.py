#!/usr/bin/env python3
"""Gather and verify every input of the installers into one directory.

    python3 packaging/assemble_inputs.py --out inputs --version nightly

Downloads the kernel (``kernel-nightly`` of lobster-os) and the QEMU archives
(``qemu-nightly`` of lobster-qemu) with ``gh``, checks each against the
release's ``SHA256SUMS``, builds the base disk with ``tools/mkbase.py`` and
writes ``VERSIONS.txt`` describing exactly what went in.
"""
import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_REPO = "jlaportebot/lobster-os"
KERNEL_TAG = "kernel-nightly"
KERNEL_FILES = ("kernel8.img", "COMMIT")
QEMU_REPO = "jlaportebot/lobster-qemu"
QEMU_TAG = "qemu-nightly"
QEMU_ARCHIVE_PATTERN = "lobster-qemu-*"
SUMS_FILE = "SHA256SUMS"
VERSIONS_FILE = "VERSIONS.txt"
DTB_FILE = "bcm2712-rpi-5-b.dtb"
BASE_IMAGE_FILE = "lobsteros.img"
HASH_CHUNK_BYTES = 1 << 20
SHA256_HEX_LENGTH = 64


def parse_sha256sums(text):
    """Map file name to hex digest from ``sha256sum`` output."""
    sums = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition(" ")
        name = name.lstrip(" *").strip()
        if len(digest) != SHA256_HEX_LENGTH or not name:
            raise ValueError(f"malformed {SUMS_FILE} line: {line!r}")
        sums[name] = digest.lower()
    return sums


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_files(directory, sums, names):
    """Raise unless every name in ``names`` is listed in ``sums`` and matches."""
    for name in names:
        expected = sums.get(name)
        if expected is None:
            raise ValueError(f"{name} is not listed in {SUMS_FILE}")
        actual = sha256_file(directory / name)
        if actual != expected:
            raise ValueError(f"{name}: sha256 {actual} != expected {expected}")


def render_versions(entries):
    """``key=value`` lines, one per entry, in the order given."""
    return "".join(f"{key}={value}\n" for key, value in entries)


def gh(*args):
    result = subprocess.run(["gh", *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def download_release(repo, tag, patterns, directory):
    command = ["release", "download", tag, "--repo", repo, "--dir", str(directory),
               "--clobber"]
    for pattern in patterns:
        command += ["--pattern", pattern]
    gh(*command)


def fetch_kernel(out):
    download_release(KERNEL_REPO, KERNEL_TAG, (*KERNEL_FILES, SUMS_FILE), out)
    sums = parse_sha256sums((out / SUMS_FILE).read_text())
    verify_files(out, sums, ("kernel8.img",))
    (out / SUMS_FILE).unlink()
    return (out / "COMMIT").read_text().strip()


def fetch_qemu(out):
    """Download every platform archive, verify them, return the archive names."""
    qemu_dir = out / "qemu"
    qemu_dir.mkdir()
    download_release(QEMU_REPO, QEMU_TAG, (QEMU_ARCHIVE_PATTERN, SUMS_FILE), qemu_dir)
    sums = parse_sha256sums((qemu_dir / SUMS_FILE).read_text())
    archives = sorted(path.name for path in qemu_dir.iterdir() if path.name != SUMS_FILE)
    if not archives:
        raise SystemExit(f"{QEMU_TAG} has no {QEMU_ARCHIVE_PATTERN} assets")
    verify_files(qemu_dir, sums, archives)
    return archives


def qemu_commit():
    return gh("release", "view", QEMU_TAG, "--repo", QEMU_REPO, "--json",
              "targetCommitish", "--jq", ".targetCommitish")


def emulator_commit():
    result = subprocess.run(["git", "rev-parse", "HEAD"], check=True, cwd=REPO_ROOT,
                            capture_output=True, text=True)
    return result.stdout.strip()


def build_base_disk(out):
    target = out / BASE_IMAGE_FILE
    subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "mkbase.py"),
                    "--output", str(target), "--force"], check=True)
    subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "mkbase.py"),
                    "--output", str(target), "--check"], check=True)


def copy_dtb(out):
    (out / DTB_FILE).write_bytes((REPO_ROOT / DTB_FILE).read_bytes())


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="empty output directory")
    parser.add_argument("--version", required=True,
                        help="release version (tag without the leading v, or 'nightly')")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    kernel_commit = fetch_kernel(out)
    archives = fetch_qemu(out)
    copy_dtb(out)
    build_base_disk(out)
    entries = [("version", args.version), ("emulator_commit", emulator_commit()),
               ("kernel_release", f"{KERNEL_REPO}@{KERNEL_TAG}"),
               ("kernel_commit", kernel_commit),
               ("kernel_sha256", sha256_file(out / "kernel8.img")),
               ("qemu_release", f"{QEMU_REPO}@{QEMU_TAG}"), ("qemu_commit", qemu_commit()),
               ("qemu_archives", ",".join(archives)),
               ("base_disk_sha256", sha256_file(out / BASE_IMAGE_FILE))]
    (out / VERSIONS_FILE).write_text(render_versions(entries))
    print((out / VERSIONS_FILE).read_text(), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
