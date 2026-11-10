#!/usr/bin/env bash
# LobsterOS VM manager entry point: ./lobstervm.sh <command> (see --help)
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m lobstervm "$@"
