#!/bin/bash
#
# run_meteo_comparison.sh
#
# Runs the meteorological data comparison utility

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# Check if OpenWeather API key is provided
if [ -z "$OPENWEATHER_API_KEY" ] && [ -z "$1" ]; then
    echo "Error: OpenWeather API key not provided"
    echo "Usage: $0 [api_key] [hour]"
    echo "  api_key: OpenWeather API key (required)"
    echo "  hour: Hour of day 0-23 (optional, default: 16)"
    echo ""
    echo "Examples:"
    echo "  $0 YOUR_API_KEY          # Uses 16:00 (4 PM)"
    echo "  $0 YOUR_API_KEY 15       # Uses 15:00 (3 PM)"
    echo ""
    echo "Or set OPENWEATHER_API_KEY environment variable"
    exit 1
fi

# Use provided key or env variable
API_KEY="${1:-$OPENWEATHER_API_KEY}"

# Load .env if exists (from project root)
if [ -f "$PROJECT_ROOT/.env" ]; then
    export $(cat "$PROJECT_ROOT/.env" | grep -v '^#' | xargs)
fi

# Change to project root
cd "$PROJECT_ROOT"

# Parse optional hour parameter (default to none, will use 16:00)
HOUR_PARAM=""
if [ ! -z "$2" ]; then
    HOUR_PARAM="--hour $2"
fi

# Run comparison (Aveiro University station only)
python3 utils/meteo_comparison.py \
    --api-key "$API_KEY" \
    --date 2026-02-17 \
    $HOUR_PARAM