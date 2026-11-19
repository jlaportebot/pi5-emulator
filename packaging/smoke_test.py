#!/usr/bin/env python3
"""Drive an installed/bundled CLI through a full VM lifecycle, with no repo or env overrides.

    python3 packaging/smoke_test.py --bundle <dir>          # <out>/LobsterVM or the .app
    python3 packaging/smoke_test.py --cli <exe> [--cli-prefix cli] --out smoke-out

Artefacts written to ``--out``: ``smoke.log`` (every command and its output),
``shot.png`` (the guest screen), ``report.json`` (timings) and, on failure,
``serial.txt`` (the guest serial console tail).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CLI_NAME = "lobstervm-cli"
VM_NAME = "ci"
DEFAULT_VM_NAME = "LobsterOS"
STEP_TIMEOUT_S = 900
SERIAL_TAIL_LINES = 200
MIN_PNG_BYTES = 2000
MIN_PIXEL_VARIANCE = 20.0
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
OVERRIDE_PREFIXES = ("LOBSTER", "XDG_", "PYTHON")
BOOT_MS_PATTERN = re.compile(r"guest boot (\d+) ms")


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


class Runner:
    """Runs CLI steps, mirrors their output to a log file and collects timings."""

    def __init__(self, command, env, log):
        self.command, self.env, self.log = command, env, log

    def run(self, args, check=True):
        line = f"$ lobstervm-cli {' '.join(args)}"
        print(line, flush=True)
        result = subprocess.run([*self.command, *args], env=self.env, capture_output=True,
                                text=True, timeout=STEP_TIMEOUT_S)
        text = (result.stdout + result.stderr).strip()
        print(text, flush=True)
        self.log.write(f"{line}\n{text}\n[exit {result.returncode}]\n")
        self.log.flush()
        if check and result.returncode != 0:
            raise SystemExit(f"FAILED (exit {result.returncode}): {' '.join(args)}")
        return result.stdout


def parse_boot_ms(output):
    """Guest boot time in ms from ``start --wait`` output, or None."""
    match = BOOT_MS_PATTERN.search(output)
    return int(match.group(1)) if match else None


def check_screenshot(path):
    """Fail unless ``path`` is a real, non-uniform PNG; return its pixel variance."""
    from PIL import Image, ImageStat

    data = path.read_bytes() if path.is_file() else b""
    if not data.startswith(PNG_MAGIC) or len(data) < MIN_PNG_BYTES:
        raise SystemExit(f"screenshot is not a real PNG ({len(data)} bytes)")
    with Image.open(path) as image:
        variance = max(ImageStat.Stat(image.convert("L")).var)
    if variance < MIN_PIXEL_VARIANCE:
        raise SystemExit(f"screenshot looks blank (pixel variance {variance:.1f})")
    print(f"screenshot ok: {len(data)} bytes, pixel variance {variance:.1f}")
    return variance


def check_quickstart(runner):
    """First-run path: quickstart creates the default VM from the bundled disk."""
    runner.run(["quickstart"])
    if DEFAULT_VM_NAME not in runner.run(["list"]):
        raise SystemExit(f"quickstart did not create VM {DEFAULT_VM_NAME}")
    runner.run(["delete", DEFAULT_VM_NAME])


def boot_and_inspect(runner, png, report):
    started = time.monotonic()
    output = runner.run(["start", VM_NAME, "--headless", "--wait"])
    report["start_to_desktop_s"] = round(time.monotonic() - started, 1)
    report["guest_boot_ms"] = parse_boot_ms(output)
    if "Uptime" not in runner.run(["shell", VM_NAME, "uptime"]):
        raise SystemExit("guest did not answer `uptime`")
    runner.run(["screenshot", VM_NAME, str(png)])
    report["screenshot_variance"] = round(check_screenshot(png), 1)
    runner.run(["stop", VM_NAME])


def lifecycle(runner, png, report, serial_out):
    check_quickstart(runner)
    runner.run(["create", VM_NAME, "--display", "none"])
    try:
        boot_and_inspect(runner, png, report)
    except (SystemExit, subprocess.SubprocessError):
        runner.run(["screenshot", VM_NAME, str(png.with_name("failure.png"))], check=False)
        serial_out.write_text(runner.run(["serial", VM_NAME, "-n", str(SERIAL_TAIL_LINES)],
                                         check=False))
        raise
    finally:
        runner.run(["stop", VM_NAME, "--force"], check=False)
        runner.run(["delete", VM_NAME], check=False)


def resolve_command(args):
    if args.bundle:
        return [str(find_cli(args.bundle.resolve()))]
    return [str(args.cli.resolve()), *args.cli_prefix]


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--bundle", type=Path, help="bundle dir or .app")
    target.add_argument("--cli", type=Path, help="installed CLI executable (or AppImage)")
    parser.add_argument("--cli-prefix", nargs="*", default=[],
                        help="arguments placed before every CLI command (AppImage: cli)")
    parser.add_argument("--out", type=Path, help="directory for logs and the screenshot")
    return parser.parse_args(argv)


def save_qemu_logs(home, out):
    """Keep every VM's qemu.log: the runtime dirs vanish with the throwaway HOME."""
    for log in home.rglob("qemu.log"):
        shutil.copyfile(log, out / f"qemu-{log.parent.name}.log")


def run_smoke(command, out):
    out.mkdir(parents=True, exist_ok=True)
    report = {}
    # AppImage runs leave a stale FUSE mount dir that cannot be deleted; ignore it.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as home_dir, \
            open(out / "smoke.log", "w") as log:
        env = clean_environment(Path(home_dir))
        try:
            lifecycle(Runner(command, env, log), out / "shot.png", report, out / "serial.txt")
        finally:
            save_qemu_logs(Path(home_dir), out)
            (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"SMOKE TEST PASSED {json.dumps(report)}")


def main(argv=None):
    args = parse_args(argv)
    run_smoke(resolve_command(args), args.out or Path("smoke-out"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
