#!/usr/bin/env bash
# Regenerate lanemerge.net.xml from the source node/edge/connection files.
# Requires netconvert (installed with eclipse-sumo).
set -e
cd "$(dirname "$0")"

netconvert \
    --node-files=lanemerge.nod.xml \
    --edge-files=lanemerge.edg.xml \
    --connection-files=lanemerge.con.xml \
    --proj "+proj=utm +zone=29 +ellps=WGS84 +datum=WGS84 +units=m +no_defs" \
    --no-turnarounds true \
    --output-file=lanemerge.net.xml \
    --no-warnings

echo "lanemerge.net.xml generated successfully."
