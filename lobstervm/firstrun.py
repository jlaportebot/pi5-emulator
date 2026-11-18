"""First-run setup for the GUI: component check and the default VM."""
from . import paths
from .config import VmConfig, VmStore

DEFAULT_VM_NAME = "LobsterOS"
DEFAULT_VM_MEMORY_GB = 4
DEFAULT_VM_RESOLUTION = "1280x720"
DEFAULT_VM_DISPLAY = "auto"


def missing_components_message():
    """A dialog-ready explanation of what is missing, or ``""`` when complete."""
    problems = paths.missing_components()
    if not problems:
        return ""
    return "LobsterVM cannot start because components are missing:\n\n" + "\n\n".join(problems)


def ensure_default_vm(store=None):
    """Create the default VM when the store has none; return its name or None."""
    store = store or VmStore()
    if store.list_names():
        return None
    store.create(VmConfig(name=DEFAULT_VM_NAME, memory_gb=DEFAULT_VM_MEMORY_GB,
                          resolution=DEFAULT_VM_RESOLUTION, display=DEFAULT_VM_DISPLAY))
    return DEFAULT_VM_NAME
