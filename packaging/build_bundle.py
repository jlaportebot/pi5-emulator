#!/usr/bin/env python3
"""Build the LobsterVM bundle: run PyInstaller, then add QEMU and the guest files.

    python3 packaging/build_bundle.py --qemu-dir <extracted lobster-qemu archive> \
        --kernel kernel8.img --dtb bcm2712-rpi-5-b.dtb --base lobsteros.img --out dist-bundle

The result follows ``packaging/LAYOUT.md``; ``<out>/LobsterVM`` (or
``<out>/LobsterVM.app`` on macOS) is what an installer ships.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

import make_icons

PACKAGING_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGING_DIR.parent
SPEC_FILE = PACKAGING_DIR / "lobstervm.spec"
APP_NAME = "LobsterVM"
MACOS_APP_RESOURCES = Path("Contents") / "Resources"
QEMU_BIN_DIR = Path("qemu") / "bin"
SHARE_DIR = Path("share") / "lobsteros"
QEMU_BINARIES = ("qemu-system-aarch64", "qemu-img")


def exe_suffix():
    return ".exe" if sys.platform.startswith("win") else ""


def run_pyinstaller(work_dir, dist_dir):
    """Build the onedir (and the .app on macOS) into ``dist_dir``."""
    make_icons.make_icons(PACKAGING_DIR / "icons")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
               "--workpath", str(work_dir), "--distpath", str(dist_dir), str(SPEC_FILE)]
    subprocess.run(command, check=True, cwd=REPO_ROOT)


def bundle_root(dist_dir):
    """The directory the components are added to (see LAYOUT.md)."""
    if sys.platform == "darwin":
        return dist_dir / f"{APP_NAME}.app" / MACOS_APP_RESOURCES
    return dist_dir / APP_NAME


def check_qemu_dir(qemu_dir):
    """The archive root must hold ``bin/`` with both QEMU executables."""
    missing = [name + exe_suffix() for name in QEMU_BINARIES
               if not (qemu_dir / "bin" / (name + exe_suffix())).is_file()]
    if missing:
        raise SystemExit(f"{qemu_dir}/bin is missing: {', '.join(missing)}")


def add_qemu(root, qemu_dir):
    check_qemu_dir(qemu_dir)
    target = root / QEMU_BIN_DIR.parent
    shutil.copytree(qemu_dir, target, symlinks=True, dirs_exist_ok=True)


def add_guest_files(root, files):
    share = root / SHARE_DIR
    share.mkdir(parents=True, exist_ok=True)
    for source, name in files:
        if not source.is_file():
            raise SystemExit(f"not a file: {source}")
        shutil.copyfile(source, share / name)


def extract_qemu_archive(archive, destination):
    """Unpack a lobster-qemu ``.tar.gz``/``.zip``; return its single top directory."""
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(destination)
    else:
        with tarfile.open(archive) as tarred:
            tarred.extractall(destination, filter="data")
    roots = [entry for entry in destination.iterdir() if entry.is_dir()]
    if len(roots) != 1:
        raise SystemExit(f"{archive.name}: expected one top directory, found {len(roots)}")
    return roots[0]


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--qemu-dir", type=Path,
                        help="extracted lobster-qemu archive root (contains bin/)")
    source.add_argument("--qemu-archive", type=Path,
                        help="lobster-qemu .tar.gz or .zip for this platform")
    parser.add_argument("--versions", type=Path, help="VERSIONS.txt to ship in share/lobsteros")
    parser.add_argument("--version", default="nightly", help="version stamped on the app")
    parser.add_argument("--kernel", type=Path, required=True, help="kernel8.img")
    parser.add_argument("--dtb", type=Path, required=True, help="bcm2712-rpi-5-b.dtb")
    parser.add_argument("--base", type=Path, required=True, help="lobsteros.img")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    return parser.parse_args(argv)


def guest_files(args):
    files = [(args.kernel, "kernel8.img"), (args.dtb, "bcm2712-rpi-5-b.dtb"),
             (args.base, "lobsteros.img")]
    if args.versions:
        files.append((args.versions, "VERSIONS.txt"))
    return files


def main(argv=None):
    args = parse_args(argv)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["LOBSTERVM_VERSION"] = args.version
    with tempfile.TemporaryDirectory(dir=out) as work:
        qemu_dir = args.qemu_dir or extract_qemu_archive(args.qemu_archive, Path(work))
        check_qemu_dir(qemu_dir)
        run_pyinstaller(Path(work) / "build", out)
        root = bundle_root(out)
        add_qemu(root, qemu_dir)
    add_guest_files(root, guest_files(args))
    print(f"bundle ready: {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
