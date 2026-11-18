"""PyInstaller entry point of the console executable (``lobstervm-cli``)."""
import sys

from lobstervm.cli import main

if __name__ == "__main__":
    sys.exit(main())
