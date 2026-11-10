#!/usr/bin/env bash
# tools/check.sh — Run all unit tests for lobstervm
#
# Equivalent to the CI gate commands: unit tests, GUI module import,
# syntax checks and CLI smoke tests.

set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")/.."

# Ensure headless display for GUI tests
export DISPLAY=""

echo "=== Running unit tests ==="
python3 -m unittest discover -s tests -v

echo ""
echo "=== Checking gui_test_vm.py syntax ==="
python3 -c "import ast,sys; ast.parse(open('tools/gui_test_vm.py').read())"
echo "Syntax OK"

echo ""
echo "=== Checking gui_test_vm.py --help ==="
python3 tools/gui_test_vm.py --help >/dev/null
echo "gui_test_vm.py --help: OK"

echo ""
echo "=== Checking lobstervm --help ==="
python3 -m lobstervm --help >/dev/null
echo "lobstervm --help: OK"

echo ""
echo "=== Checking lobstervm gui --help ==="
python3 -m lobstervm gui --help >/dev/null
echo "lobstervm gui --help: OK"

echo ""
echo "=== Checking GUI module imports ==="
python3 -c "import lobstervm.gui; print('gui import ok')"

echo ""
echo "All tests passed!"
