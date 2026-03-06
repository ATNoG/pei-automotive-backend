#!/bin/bash
#
# run_subscribe_stations.sh
#
# Runs the station assignment subscriber utility to monitor MQTT messages

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

echo "Station Assignment Subscriber"
echo "=============================="
echo "This utility subscribes to cars/station/+ MQTT topic"
echo "and displays station assignments for all cars."
echo ""
echo "Press Ctrl+C to exit"
echo ""

# Run the subscriber
cd "$PROJECT_ROOT" || exit 1
python3 utils/subscribe_stations.py
