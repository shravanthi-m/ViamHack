#!/bin/sh
# Explicitly create a NEW candidate; never silently replace a rehearsed pack.
set -eu
if [ "$#" -ne 1 ]; then
    echo 'Usage: sh demos/prepare_fixed.sh runs/NEW_PACK_DIRECTORY' >&2
    exit 2
fi
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo"
config=${DEMO_CONFIG:-config/local.json}
.venv/bin/python -m runtime --config "$config" prepare-demo --compact --out "$1"
for task in signature reset shake; do
    .venv/bin/python -m runtime --config "$config" fixed-task "$task" --pack "$1"
done
