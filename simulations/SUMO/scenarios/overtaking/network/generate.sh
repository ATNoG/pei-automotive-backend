#!/usr/bin/env bash
# Regenerate overtaking.net.xml from source node/edge files.
# Run this whenever overtaking.nod.xml or overtaking.edg.xml change.
# Requires SUMO's netconvert binary on PATH.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
netconvert \
    --node-files  "$SCRIPT_DIR/overtaking.nod.xml" \
    --edge-files  "$SCRIPT_DIR/overtaking.edg.xml" \
    --output-file "$SCRIPT_DIR/overtaking.net.xml" \
    --proj "+proj=utm +zone=29 +ellps=WGS84 +datum=WGS84 +units=m +no_defs" \
    --offset.x -522636.0363 \
    --offset.y -4497497.6291
echo "Generated $SCRIPT_DIR/overtaking.net.xml"
