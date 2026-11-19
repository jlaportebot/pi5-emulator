#!/usr/bin/env python3
"""Generate the platform icon files from ``assets/lobsteros-shell-icon.png``.

    python3 packaging/make_icons.py --out packaging/icons

Always writes ``lobstervm.png`` and ``lobstervm.ico``; on macOS also
``lobstervm.icns`` (via ``iconutil``).
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ICON = REPO_ROOT / "assets" / "lobsteros-shell-icon.png"
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
ICONSET_POINT_SIZES = (16, 32, 128, 256, 512)
RETINA_FACTOR = 2


def iconset_entries():
    """``(file name, pixel size)`` pairs of a macOS ``.iconset`` directory."""
    entries = []
    for points in ICONSET_POINT_SIZES:
        entries.append((f"icon_{points}x{points}.png", points))
        entries.append((f"icon_{points}x{points}@2x.png", points * RETINA_FACTOR))
    return entries


def write_iconset(source, directory):
    for name, pixels in iconset_entries():
        resized = source.resize((pixels, pixels), Image.LANCZOS)
        resized.save(directory / name)


def write_icns(source, target):
    with tempfile.TemporaryDirectory() as work:
        iconset = Path(work) / "lobstervm.iconset"
        iconset.mkdir()
        write_iconset(source, iconset)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(target)],
                       check=True)


def make_icons(out, source_path=SOURCE_ICON):
    """Write the icon files into ``out``; return the list of paths written."""
    out.mkdir(parents=True, exist_ok=True)
    source = Image.open(source_path).convert("RGBA")
    written = [out / "lobstervm.png", out / "lobstervm.ico"]
    source.save(written[0])
    source.save(written[1], sizes=ICO_SIZES)
    if sys.platform == "darwin":
        written.append(out / "lobstervm.icns")
        write_icns(source, written[-1])
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    for path in make_icons(parser.parse_args(argv).out):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
