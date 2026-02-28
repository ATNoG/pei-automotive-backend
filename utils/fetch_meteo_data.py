#!/usr/bin/env python3
#
# fetch_meteo_data.py
#
# Simple utility to fetch and display meteo data from Ditto
#
import sys
import json
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from common.config import load_config
from common.ditto_rest_client import DittoRestClient
from common.models import Station


def main():
    print("Fetching Meteorological Data from Ditto\n")
    
    # Load configuration
    try:
        config = load_config()
    except Exception as e:
        print(f"Error loading configuration: {e}")
        sys.exit(1)
    
    # Create Weather API client
    weather_client = DittoRestClient(
        api_url=config.weather_api_url,
        username=config.weather_username,
        password=config.weather_password,
    )
    
    # Fetch all meteo things
    print(f"\nFetching meteo things from {config.weather_api_url}...")
    meteo_things = weather_client.get_all_meteo_things()
    
    if not meteo_things:
        print("\nNo meteorological things found!")
        return
    
    print(f"Found {len(meteo_things)} meteorological stations\n")
    
    # Parse and display each station
    for i, thing_data in enumerate(meteo_things, 1):
        print(f"\n{i}. Station Details:")
        
        try:
            station = Station.from_ditto_thing(thing_data)
            
            print(f"   ID: {station.station_id}")
            print(f"   Name: {station.location_name}")
            print(f"   Location: ({station.location.latitude}, {station.location.longitude})")
            
            if station.measurement:
                m = station.measurement
                print(f"\n   Measurements (at {m.time}):")
                print(f"      Temperature: {m.temperature}°C")
                print(f"      Humidity: {m.humidity}%")
                print(f"      Pressure: {m.pressure} hPa")
                print(f"      Wind Intensity: {m.wind_intensity} km/h")
                print(f"      Wind Direction: {m.wind_direction.name} ({int(m.wind_direction)})")
                print(f"      Radiation: {m.radiation} W/m²")
                print(f"      Precipitation: {m.accumulated_precipitation} mm")
            else:
                print("\n   No measurement data available")
                
        except Exception as e:
            print(f"   Error parsing station: {e}")
            print(f"   Raw data: {json.dumps(thing_data, indent=2)}")
    
    print(f"\nTotal: {len(meteo_things)} stations")


if __name__ == "__main__":
    main()