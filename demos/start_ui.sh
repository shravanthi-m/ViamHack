#!/bin/sh
# Run in an interactive operator terminal for supervised execution.
set -eu
mode=${1:---preview}
if [ "$#" -gt 1 ]; then
    echo 'Usage: sh demos/start_ui.sh [--preview|--execute]' >&2
    exit 2
fi
case "$mode" in --preview|--execute) ;; *) echo 'Choose --preview or --execute.' >&2; exit 2 ;; esac
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo"
config=${DEMO_CONFIG:-config/local.json}
port=${DEMO_PORT:-8765}
if [ "$mode" = --execute ]; then
    : "${DEMO_PACK:?Set DEMO_PACK to the exact frozen pack you prepared.}"
    exec .venv/bin/python -m runtime --config "$config" observer --view overhead --port "$port" \
        --demo-pack "$DEMO_PACK" --enable-demo-execution
fi
exec .venv/bin/python -m runtime --config "$config" observer --view overhead --port "$port"
