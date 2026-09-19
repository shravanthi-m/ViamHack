#!/bin/sh
# Default is an offline check. Execution always retains terminal gates.
set -eu
task=${1:-}
mode=${2:---check}
if [ "$#" -gt 2 ]; then
    echo 'Usage: sh demos/fixed_tasks.sh signature|reset|shake|shaker [--check|--execute]' >&2
    exit 2
fi
case "$task" in signature|reset|shake|shaker) ;; *) echo 'Choose signature, reset, shake, or shaker.' >&2; exit 2 ;; esac
case "$mode" in --check|--execute) ;; *) echo 'Choose --check or --execute.' >&2; exit 2 ;; esac
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo"
: "${DEMO_PACK:?Set DEMO_PACK to the exact frozen pack you prepared.}"
config=${DEMO_CONFIG:-config/local.json}
if [ "$mode" = --execute ]; then
    exec .venv/bin/python -m runtime --config "$config" fixed-task "$task" --pack "$DEMO_PACK" --execute
fi
exec .venv/bin/python -m runtime --config "$config" fixed-task "$task" --pack "$DEMO_PACK"
