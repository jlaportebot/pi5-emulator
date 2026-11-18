"""PyInstaller entry point of the windowed executable (``LobsterVM``)."""
import sys

from lobstervm.gui import main

if __name__ == "__main__":
    sys.exit(main())
