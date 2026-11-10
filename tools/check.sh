#!/usr/bin/env bash
# Quick local check script for lobstervm - runs unit tests and syntax checks
# Equivalent to the CI gate commands.

set -euo pipefail

cd "$(dirname "$0")/.."

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
echo "=== All checks passed ==="