#!/usr/bin/env python3
"""Compatibility entry point: the GUI harness lives in gui_test_vm.py.

`python3 tools/gui_test.py [--kernel PATH] [--test NAME]` keeps working and
runs the same suite on a throwaway lobstervm VM (unique runtime paths, no
fixed /tmp sockets, so several runs can coexist).
"""
import sys

from gui_test_vm import main

if __name__ == "__main__":
    sys.exit(main())
