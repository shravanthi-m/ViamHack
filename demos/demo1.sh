#!/bin/sh
# Script 1: recorded coconut pour/return, then pitcher pour/return.
# Default checks offline. --execute preserves the runtime's operator gates.
set -eu
if [ "$#" -gt 1 ]; then
    echo 'Usage: sh demos/demo1.sh [--check|--execute]' >&2
    exit 2
fi
case "${1:---check}" in
    --check) mode=check ;;
    --execute) mode=execute ;;
    *) echo 'Usage: sh demos/demo1.sh [--check|--execute]' >&2; exit 2 ;;
esac
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo"
if [ "$mode" = execute ]; then
    exec .venv/bin/python -m runtime --config config/local.json prepared-demo runs/demo1_v1 --execute
fi
exec .venv/bin/python -m runtime --config config/local.json prepared-demo runs/demo1_v1
