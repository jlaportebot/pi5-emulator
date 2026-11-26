"""AppRun dispatch: no arguments starts the GUI, any arguments run the CLI."""
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
MAKE_APPIMAGE = REPO_DIR / "packaging" / "make_appimage.sh"
APPRUN_PATTERN = re.compile(r"<<'APPRUN'\n(.*?)\nAPPRUN\n", re.DOTALL)
STUB = "#!/bin/sh\necho \"{name} $*\"\n"


def extract_apprun_text():
    match = APPRUN_PATTERN.search(MAKE_APPIMAGE.read_text())
    if match is None:
        raise AssertionError("AppRun heredoc not found in make_appimage.sh")
    return match.group(1) + "\n"


def write_executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@unittest.skipIf(sys.platform == "win32", "AppRun is the Linux AppImage's POSIX shell entry point")
class AppRunDispatchTest(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)
        self.appdir = self.tmp / "appdir"
        payload = self.appdir / "usr" / "lib" / "LobsterVM"
        write_executable(payload / "LobsterVM", STUB.format(name="GUI"))
        write_executable(payload / "lobstervm-cli", STUB.format(name="CLI"))
        versions = payload / "share" / "lobsteros" / "VERSIONS.txt"
        versions.parent.mkdir(parents=True)
        versions.write_text("test\n")
        apprun = self.appdir / "AppRun"
        apprun.write_text(extract_apprun_text())
        apprun.chmod(0o755)
        self.apprun = apprun
        self.env = dict(os.environ, XDG_DATA_HOME=str(self.tmp / "data"))

    def run_apprun(self, *args):
        result = subprocess.run(
            [str(self.apprun), *args], env=self.env,
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_no_arguments_starts_gui_without_arguments(self):
        self.assertEqual(self.run_apprun(), "GUI")

    def test_create_demo_runs_cli(self):
        self.assertEqual(self.run_apprun("create", "demo"), "CLI create demo")

    def test_leading_cli_is_dropped(self):
        self.assertEqual(self.run_apprun("cli", "list"), "CLI list")

    def test_help_runs_cli(self):
        self.assertEqual(self.run_apprun("--help"), "CLI --help")


if __name__ == "__main__":
    unittest.main()
