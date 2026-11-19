#!/usr/bin/env python3
"""Compile the Inno Setup installer from a built bundle.

    python3 packaging/make_windows_installer.py --bundle dist/LobsterVM \
        --version nightly --out installers
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PACKAGING_DIR = Path(__file__).resolve().parent
SCRIPT = PACKAGING_DIR / "lobstervm.iss"
ICON = PACKAGING_DIR / "icons" / "lobstervm.ico"
COMPILER_NAMES = ("ISCC.exe", "iscc")
DEFAULT_COMPILER_DIRS = ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")
INNO_SUBDIRS = ("Inno Setup 6", "Programs/Inno Setup 6")


def find_compiler():
    """The Inno Setup command-line compiler, from PATH or a default install dir."""
    for name in COMPILER_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)
    for variable in DEFAULT_COMPILER_DIRS:
        for subdir in INNO_SUBDIRS:
            candidate = Path(os.environ.get(variable, "")) / subdir / "ISCC.exe"
            if candidate.is_file():
                return candidate
    raise SystemExit("Inno Setup compiler (ISCC.exe) not found; choco install innosetup")


def installer_name(version):
    return f"LobsterVM-Setup-{version}-x64.exe"


def compile_installer(compiler, bundle, version, out):
    out.mkdir(parents=True, exist_ok=True)
    command = [str(compiler), "/Q", f"/DAppVersion={version}", f"/DSourceDir={bundle}",
               f"/DOutputDir={out}", f"/DIconFile={ICON}", str(SCRIPT)]
    subprocess.run(command, check=True)
    result = out / installer_name(version)
    if not result.is_file():
        raise SystemExit(f"compiler succeeded but {result} was not produced")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bundle", type=Path, required=True, help="dist/LobsterVM")
    parser.add_argument("--version", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    result = compile_installer(find_compiler(), args.bundle.resolve(), args.version,
                               args.out.resolve())
    print(f"installer: {result} ({result.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
