#!/usr/bin/env python3
"""Drive the bundled CLI through a full VM lifecycle with no repo or env overrides.

    python3 packaging/smoke_test.py --bundle <dir>   # <out>/LobsterVM or the .app
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CLI_NAME = "lobstervm-cli"
VM_NAME = "t"
STEP_TIMEOUT_S = 240
MIN_PNG_BYTES = 2000
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
OVERRIDE_PREFIXES = ("LOBSTER", "XDG_", "PYTHON")


def find_cli(bundle):
    """The console executable inside a bundle (Windows/Linux dir or macOS .app)."""
    suffix = ".exe" if sys.platform.startswith("win") else ""
    for candidate in (bundle / (CLI_NAME + suffix),
                      bundle / "Contents" / "MacOS" / CLI_NAME):
        if candidate.is_file():
            return candidate
    raise SystemExit(f"{CLI_NAME} not found in {bundle}")


def clean_environment(home):
    """The caller's environment minus every override, rooted in a throwaway HOME."""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(OVERRIDE_PREFIXES)}
    env.update(HOME=str(home), USERPROFILE=str(home), LOCALAPPDATA=str(home / "local"),
               TMPDIR=str(home / "tmp"), TEMP=str(home / "tmp"), TMP=str(home / "tmp"))
    (home / "local").mkdir(exist_ok=True)
    (home / "tmp").mkdir(exist_ok=True)
    return env


def run_step(cli, env, args):
    print(f"$ lobstervm-cli {' '.join(args)}", flush=True)
    result = subprocess.run([str(cli), *args], env=env, capture_output=True, text=True,
                            timeout=STEP_TIMEOUT_S)
    print((result.stdout + result.stderr).strip(), flush=True)
    if result.returncode != 0:
        raise SystemExit(f"FAILED (exit {result.returncode}): {' '.join(args)}")
    return result.stdout


def check_screenshot(path):
    data = path.read_bytes() if path.is_file() else b""
    if not data.startswith(PNG_MAGIC) or len(data) < MIN_PNG_BYTES:
        raise SystemExit(f"screenshot is not a real PNG ({len(data)} bytes)")
    print(f"screenshot ok: {len(data)} bytes")


def lifecycle(cli, env, png):
    run_step(cli, env, ["create", VM_NAME, "--display", "none"])
    try:
        run_step(cli, env, ["start", VM_NAME, "--headless", "--wait"])
        if "Uptime" not in run_step(cli, env, ["shell", VM_NAME, "uptime"]):
            raise SystemExit("guest did not answer `uptime`")
        run_step(cli, env, ["screenshot", VM_NAME, str(png)])
        check_screenshot(png)
        run_step(cli, env, ["stop", VM_NAME])
    finally:
        run_step(cli, env, ["stop", VM_NAME])
        run_step(cli, env, ["delete", VM_NAME])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args(argv)
    cli = find_cli(args.bundle.resolve())
    with tempfile.TemporaryDirectory() as home_dir:
        home = Path(home_dir)
        lifecycle(cli, clean_environment(home), home / "shot.png")
    print("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
