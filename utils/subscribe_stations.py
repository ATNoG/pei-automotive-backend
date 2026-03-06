#!/usr/bin/env python3
#
# subscribe_stations.py
#
# Simple subscriber to test station assignment MQTT messages
#
import sys
import json
import signal
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from common.config import load_config
from common.mqtt_client import MQTTClient


def process_station_assignment(payload: str):
    """Process received station assignment messages"""
    try:
        data = json.loads(payload)
        
        car_id = data.get('car_id')
        station = data.get('station', {})
        timestamp = data.get('timestamp')
        
        print("Station Assignment Update")
        print(f"Car ID: {car_id}")
        print(f"Timestamp: {timestamp}")
        print("\nAssigned Station:")
        print(f"  ID: {station.get('station_id')}")
        print(f"  Name: {station.get('location_name')}")
        
        location = station.get('location', {})
        print(f"  Location: ({location.get('latitude')}, {location.get('longitude')})")
        
        measurement = station.get('measurement')
        if measurement:
            print("\n  Current Weather:")
            print(f"    Temperature: {measurement.get('temperature')}°C")
            print(f"    Humidity: {measurement.get('humidity')}%")
            print(f"    Pressure: {measurement.get('pressure')} hPa")
            print(f"    Wind Intensity: {measurement.get('wind_intensity')} km/h")
            print(f"    Wind Direction: {measurement.get('wind_direction')}")
            print(f"    Radiation: {measurement.get('radiation')} W/m²")
            print(f"    Precipitation: {measurement.get('accumulated_precipitation')} mm")
        else:
            print("\n  Weather: No measurements available")
        
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON: {e}")
        print(f"Raw payload: {payload[:500]}")
    except Exception as e:
        print(f"Error processing message: {e}")


def main():
    print("Station Assignment MQTT Subscriber")
    print("Press Ctrl+C to exit\n")
    
    # Load configuration
    config = load_config()
    
    # For host-based scripts, replace Docker service name with localhost
    broker_host = config.broker_host
    if broker_host == "mosquitto_broker":
        broker_host = "localhost"
        print(f"Note: Using localhost instead of {config.broker_host} for host access\n")
    
    # Create MQTT client
    mqtt_client = MQTTClient(
        host=broker_host,
        port=config.broker_port,
        username=config.broker_user,
        password=config.broker_password,
        client_id="station-assignment-subscriber-test",
    )
    
    # Connect and subscribe
    try:
        mqtt_client.connect()
        print(f"Connected to MQTT broker at {broker_host}:{config.broker_port}")
        
        # Subscribe to wildcard topic to get all car station assignments
        topic_wildcard = f"{config.station_assignment_topic_base}/+"
        mqtt_client.subscribe(topic_wildcard, process_station_assignment)
        print(f"Subscribed to topic: {topic_wildcard}")
        print("\nWaiting for station assignment messages...\n")
    except Exception as e:
        print(f"Failed to connect to MQTT broker: {e}")
        print("\nMake sure Docker services are running and the MQTT broker is accessible:")
        print(f"  Trying to connect to: {broker_host}:{config.broker_port}")
        sys.exit(1)
    
    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        print("\nShutting down...")
        mqtt_client.disconnect()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    # Keep running
    mqtt_client.loop_forever()


if __name__ == "__main__":
    main()
