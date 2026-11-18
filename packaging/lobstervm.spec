# PyInstaller spec: one onedir holding the console CLI and the windowed GUI.
# Run from the repository root: pyinstaller packaging/lobstervm.spec
import sys
from pathlib import Path

PACKAGING_DIR = Path(SPECPATH)  # noqa: F821 - injected by PyInstaller
REPO_ROOT = PACKAGING_DIR.parent
APP_NAME = "LobsterVM"
CLI_NAME = "lobstervm-cli"
HIDDEN_IMPORTS = ["lobstervm.gui", "tkinter"]
EXCLUDES = ["PIL", "numpy"]


def analyse(entry):
    return Analysis(  # noqa: F821
        [str(PACKAGING_DIR / entry)], pathex=[str(REPO_ROOT)],
        hiddenimports=HIDDEN_IMPORTS, excludes=EXCLUDES)


def executable(analysis, name, windowed):
    return EXE(  # noqa: F821
        PYZ(analysis.pure), analysis.scripts, [], exclude_binaries=True,  # noqa: F821
        name=name, console=not windowed, upx=False)


cli_analysis = analyse("entry_cli.py")
gui_analysis = analyse("entry_gui.py")

cli_exe = executable(cli_analysis, CLI_NAME, windowed=False)
gui_exe = executable(gui_analysis, APP_NAME, windowed=True)
collected = COLLECT(  # noqa: F821
    cli_exe, cli_analysis.binaries, cli_analysis.datas,
    gui_exe, gui_analysis.binaries, gui_analysis.datas,
    upx=False, name=APP_NAME)

if sys.platform == "darwin":
    BUNDLE(  # noqa: F821
        collected, name=f"{APP_NAME}.app", bundle_identifier="io.github.jlaportebot.lobstervm")
