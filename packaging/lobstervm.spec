# PyInstaller spec: one onedir holding the console CLI and the windowed GUI.
# Run from the repository root: pyinstaller packaging/lobstervm.spec
import os
import re
import sys
from pathlib import Path

PACKAGING_DIR = Path(SPECPATH)  # noqa: F821 - injected by PyInstaller
REPO_ROOT = PACKAGING_DIR.parent
APP_NAME = "LobsterVM"
CLI_NAME = "lobstervm-cli"
HIDDEN_IMPORTS = ["lobstervm.gui", "tkinter"]
EXCLUDES = ["PIL", "numpy"]
ICON_DIR = PACKAGING_DIR / "icons"  # written by make_icons.py before the build
ICON_FILES = {"win32": "lobstervm.ico", "darwin": "lobstervm.icns"}


def analyse(entry):
    return Analysis(  # noqa: F821
        [str(PACKAGING_DIR / entry)], pathex=[str(REPO_ROOT)],
        hiddenimports=HIDDEN_IMPORTS, excludes=EXCLUDES)


def icon_path():
    candidate = ICON_DIR / ICON_FILES.get(sys.platform, "")
    return str(candidate) if candidate.is_file() else None


def plist_version():
    """Dotted-numeric version for Info.plist; ``0.0.0`` for nightly builds."""
    version = os.environ.get("LOBSTERVM_VERSION", "")
    return version if re.fullmatch(r"\d+(\.\d+){0,2}", version) else "0.0.0"


def executable(analysis, name, windowed):
    return EXE(  # noqa: F821
        PYZ(analysis.pure), analysis.scripts, [], exclude_binaries=True,  # noqa: F821
        name=name, console=not windowed, upx=False, icon=icon_path())


cli_analysis = analyse("entry_cli.py")
gui_analysis = analyse("entry_gui.py")

cli_exe = executable(cli_analysis, CLI_NAME, windowed=False)
gui_exe = executable(gui_analysis, APP_NAME, windowed=True)
collected = COLLECT(  # noqa: F821
    # The GUI comes first: PyInstaller makes the first executable the .app's main one.
    gui_exe, gui_analysis.binaries, gui_analysis.datas,
    cli_exe, cli_analysis.binaries, cli_analysis.datas,
    upx=False, name=APP_NAME)

if sys.platform == "darwin":
    BUNDLE(  # noqa: F821
        collected, name=f"{APP_NAME}.app", icon=icon_path(),
        bundle_identifier="io.github.jlaportebot.lobstervm",
        info_plist={"CFBundleDisplayName": APP_NAME, "NSHighResolutionCapable": True,
                    "LSMinimumSystemVersion": "12.0",
                    "CFBundleShortVersionString": plist_version(),
                    "CFBundleVersion": plist_version()})
