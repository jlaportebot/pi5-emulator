"""lobstervm — a VirtualBox-style manager for the LobsterOS Pi 5 emulator.

Public API:
    VmConfig, VmStore  — persistent VM definitions
    Machine            — control of one running (or stopped) VM
"""
from .config import VmConfig, VmStore, VmError
from .machine import Machine

__all__ = ["VmConfig", "VmStore", "VmError", "Machine"]
__version__ = "1.0.0"
