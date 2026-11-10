"""Command-line front end: ``lobstervm <command>`` (think VBoxManage)."""
import argparse
import sys
import time
from dataclasses import fields

from . import paths
from .config import MACHINE_BY_MEMORY_GB, DISPLAY_BACKENDS, VmConfig, VmError, VmStore
from .machine import Machine

DEFAULT_VM = "lobsteros"
TAIL_DEFAULT_LINES = 40


def _add_config_options(parser):
    parser.add_argument("--memory", type=int, dest="memory_gb",
                        choices=sorted(MACHINE_BY_MEMORY_GB), help="RAM in GB")
    parser.add_argument("--resolution", help="e.g. 1920x1080")
    parser.add_argument("--display", choices=DISPLAY_BACKENDS)
    parser.add_argument("--audio", choices=("on", "off"))
    parser.add_argument("--share", dest="shared_folder",
                        help="host directory exposed to the guest as /share")
    parser.add_argument("--kernel", help="kernel8.img to boot (default: bundled)")
    parser.add_argument("--description")


def _apply_config_options(config, args):
    for field in ("memory_gb", "resolution", "display", "shared_folder",
                  "kernel", "description"):
        value = getattr(args, field, None)
        if value is not None:
            setattr(config, field, value)
    if getattr(args, "audio", None) is not None:
        config.audio = args.audio == "on"


def cmd_list(store, args):
    names = store.list_names()
    if not names:
        print("No VMs. Create one with: lobstervm create")
        return 0
    for name in names:
        machine = Machine(name, store)
        config = machine.config
        print(f"{name:<20} {machine.status():<8} {config.memory_gb}GB "
              f"{config.resolution} display={config.display}")
    return 0


def cmd_create(store, args):
    config = VmConfig(name=args.name)
    _apply_config_options(config, args)
    store.create(config)
    print(f"Created VM {args.name} in {store.vm_dir(args.name)}")
    return 0


def cmd_modify(store, args):
    config = store.load(args.name)
    _apply_config_options(config, args)
    store.save(config)
    print(f"Updated VM {args.name}")
    return 0


def cmd_show(store, args):
    machine = Machine(args.name, store)
    print(f"status       {machine.status()}")
    for field in fields(VmConfig):
        print(f"{field.name:<12} {getattr(machine.config, field.name)}")
    print(f"disk         {store.disk_path(args.name)}")
    print(f"runtime      {machine.runtime_dir}")
    return 0


def cmd_delete(store, args):
    machine = Machine(args.name, store)
    if machine.is_running():
        raise VmError(f"VM {args.name} is running; stop it first")
    store.delete(args.name)
    print(f"Deleted VM {args.name}")
    return 0


def cmd_start(store, args):
    if not store.exists(args.name) and args.name == DEFAULT_VM:
        store.create(VmConfig(name=DEFAULT_VM))
        print(f"Created default VM {DEFAULT_VM}")
    machine = Machine(args.name, store)
    display = "none" if args.headless else args.display
    pid = machine.start(display=display, resume=args.resume)
    print(f"Started {args.name} (pid {pid})")
    if args.wait:
        boot_ms = machine.wait_desktop()
        print(f"Desktop ready (guest boot {boot_ms} ms)")
    return 0


def cmd_stop(store, args):
    machine = Machine(args.name, store)
    if args.force:
        machine.poweroff()
    else:
        machine.stop()
    print(f"Stopped {args.name}")
    return 0


def cmd_simple(action):
    def run(store, args):
        getattr(Machine(args.name, store), action)()
        print(f"{action}: {args.name}")
        return 0
    return run


def cmd_factory_reset(store, args):
    machine = Machine(args.name, store)
    if machine.is_running():
        raise VmError(f"VM {args.name} is running; stop it first")
    store.reset_disk(args.name)
    print(f"Disk of {args.name} reset to the pristine system image")
    return 0


def cmd_snapshot(store, args):
    machine = Machine(args.name, store)
    if args.action == "list":
        for tag in machine.snapshot_list():
            print(tag)
        return 0
    if not args.tag:
        raise VmError(f"snapshot {args.action} needs a tag")
    if args.action == "take":
        machine.snapshot_take(args.tag)
    elif args.action == "restore":
        machine.snapshot_restore(args.tag)
    else:
        machine.snapshot_delete(args.tag)
    print(f"snapshot {args.action}: {args.tag}")
    return 0


def cmd_screenshot(store, args):
    print(Machine(args.name, store).screenshot(args.path))
    return 0


def cmd_click(store, args):
    machine = Machine(args.name, store)
    if args.double:
        machine.double_click(args.x, args.y)
    else:
        machine.click(args.x, args.y, button=2 if args.right else 1)
    return 0


def cmd_move(store, args):
    Machine(args.name, store).mouse_move(args.x, args.y)
    return 0


def cmd_drag(store, args):
    Machine(args.name, store).drag(args.x0, args.y0, args.x1, args.y1)
    return 0


def cmd_key(store, args):
    Machine(args.name, store).key(int(args.scancode, 0), " ".join(args.mods))
    return 0


def cmd_type(store, args):
    Machine(args.name, store).type_serial(args.text.replace("\\n", "\n"))
    return 0


def cmd_shell(store, args):
    print(Machine(args.name, store).shell(" ".join(args.command), args.timeout))
    return 0


def cmd_hmp(store, args):
    print(Machine(args.name, store).hmp(" ".join(args.command)), end="")
    return 0


def cmd_serial(store, args):
    machine = Machine(args.name, store)
    text = machine.serial_bytes().decode("utf-8", "replace")
    print("\n".join(text.splitlines()[-args.lines:]))
    offset = len(machine.serial_bytes())
    while args.follow and machine.is_running():
        data = machine.serial_bytes()
        if len(data) > offset:
            sys.stdout.write(data[offset:].decode("utf-8", "replace"))
            sys.stdout.flush()
            offset = len(data)
        time.sleep(0.3)
    return 0


def cmd_doctor(store, args):
    checks = [("QEMU fork", paths.qemu_binary), ("kernel", paths.default_kernel),
              ("device tree", paths.default_dtb), ("base disk", paths.base_disk)]
    failed = 0
    for label, finder in checks:
        try:
            print(f"ok    {label:<12} {finder()}")
        except paths.MissingComponent as error:
            failed += 1
            print(f"FAIL  {label:<12} {error}")
    print(f"      {'VM store':<12} {store.root}")
    return 1 if failed else 0


def cmd_gui(store, args):
    from .gui import run_manager
    return run_manager(store)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="lobstervm", description="LobsterOS Pi 5 virtual machine manager")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, handler, help_text, named=True):
        command = sub.add_parser(name, help=help_text)
        command.set_defaults(handler=handler)
        if named:
            command.add_argument("name", nargs="?", default=DEFAULT_VM,
                                 help=f"VM name (default: {DEFAULT_VM})")
        return command

    add("list", cmd_list, "list VMs and their state", named=False)
    add("doctor", cmd_doctor, "check that QEMU, kernel and disk are found", named=False)
    add("gui", cmd_gui, "open the graphical VM manager", named=False)
    _add_config_options(add("create", cmd_create, "create a VM"))
    _add_config_options(add("modify", cmd_modify, "change a VM's settings"))
    add("show", cmd_show, "show a VM's settings and state")
    add("delete", cmd_delete, "delete a VM and its disk")
    start = add("start", cmd_start, "boot a VM")
    start.add_argument("--headless", action="store_true", help="no window")
    start.add_argument("--display", choices=DISPLAY_BACKENDS)
    start.add_argument("--resume", metavar="TAG", help="restore a snapshot")
    start.add_argument("--wait", action="store_true", help="wait for the desktop")
    stop = add("stop", cmd_stop, "shut the guest down cleanly")
    stop.add_argument("--force", action="store_true", help="power off immediately")
    add("reset", cmd_simple("reset"), "hard-reset the VM")
    add("pause", cmd_simple("pause"), "freeze the VM")
    add("resume", cmd_simple("resume"), "unfreeze the VM")
    add("factory-reset", cmd_factory_reset, "discard all guest disk changes")
    _add_runtime_commands(add)
    return parser


def _add_runtime_commands(add):
    snapshot = add("snapshot", cmd_snapshot, "take/restore/list/delete snapshots")
    snapshot.add_argument("action", choices=("take", "restore", "list", "delete"))
    snapshot.add_argument("tag", nargs="?")
    add("screenshot", cmd_screenshot, "save the guest screen").add_argument("path")
    click = add("click", cmd_click, "click at guest coordinates")
    click.add_argument("x", type=int)
    click.add_argument("y", type=int)
    click.add_argument("--double", action="store_true")
    click.add_argument("--right", action="store_true")
    move = add("move", cmd_move, "move the guest pointer")
    move.add_argument("x", type=int)
    move.add_argument("y", type=int)
    drag = add("drag", cmd_drag, "drag from one point to another")
    for coordinate in ("x0", "y0", "x1", "y1"):
        drag.add_argument(coordinate, type=int)
    key = add("key", cmd_key, "press a Set-1 scancode, e.g. 0x1C [ctrl]")
    key.add_argument("scancode")
    key.add_argument("mods", nargs="*")
    add("type", cmd_type, "type text on the serial console").add_argument("text")
    shell = add("shell", cmd_shell, "run a guest shell command, print output")
    shell.add_argument("command", nargs="+")
    shell.add_argument("--timeout", type=float, default=60.0)
    add("hmp", cmd_hmp, "run a raw QEMU monitor command").add_argument(
        "command", nargs="+")
    serial = add("serial", cmd_serial, "show the guest serial log")
    serial.add_argument("-n", "--lines", type=int, default=TAIL_DEFAULT_LINES)
    serial.add_argument("-f", "--follow", action="store_true")


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(VmStore(), args)
    except (VmError, paths.MissingComponent) as error:
        print(f"lobstervm: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
