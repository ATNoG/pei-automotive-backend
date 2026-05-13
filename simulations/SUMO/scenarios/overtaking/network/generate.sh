#!/usr/bin/env bash
# Regenerate overtaking.net.xml from source primitives.
# Run from the network/ directory:  bash generate.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

netconvert \
    --node-files  "$SCRIPT_DIR/overtaking.nod.xml" \
    --edge-files  "$SCRIPT_DIR/overtaking.edg.xml" \
    --output-file "$SCRIPT_DIR/overtaking.net.xml" \
    --no-turnarounds true \
    --lefthand false \
    2>&1
