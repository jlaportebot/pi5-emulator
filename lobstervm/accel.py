"""CPU acceleration choice for a VM: TCG (software, the default) or KVM."""
import os
import platform
import sys

TCG = "tcg"
KVM = "kvm"
ACCELERATORS = (TCG, KVM)
DEFAULT_ACCEL = TCG
KVM_DEVICE = "/dev/kvm"
AARCH64_MACHINES = ("aarch64", "arm64")
#: The core model TCG emulates; KVM runs the guest on the host's own cores.
TCG_CPU = "cortex-a76"
HOST_CPU = "host"
SWITCH_BACK_HINT = "switch the VM back with `lobstervm modify <vm> --accel tcg`"


def kvm_unavailable_reason(host_platform=None, machine=None, access=None):
    """Why KVM cannot be used on this host, or ``None`` when it can.

    The three probes are injectable so the decision table can be unit-tested.
    """
    host_platform = sys.platform if host_platform is None else host_platform
    machine = platform.machine() if machine is None else machine
    access = _kvm_device_accessible if access is None else access
    if not host_platform.startswith("linux"):
        return f"KVM needs a Linux host (this is {host_platform})"
    if machine.lower() not in AARCH64_MACHINES:
        return f"KVM needs an aarch64 host CPU (this is {machine})"
    if not access(KVM_DEVICE):
        return f"{KVM_DEVICE} is missing or not readable and writable by this user"
    return None


def kvm_available():
    return kvm_unavailable_reason() is None


def _kvm_device_accessible(device):
    return os.access(device, os.R_OK | os.W_OK)


def qemu_arguments(accel):
    """The ``-accel`` and ``-cpu`` options for ``accel``."""
    if accel == KVM:
        return ["-accel", KVM, "-cpu", HOST_CPU]
    return ["-accel", TCG, "-cpu", TCG_CPU]
