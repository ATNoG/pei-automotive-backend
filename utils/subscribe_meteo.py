#!/usr/bin/env python3
#
# subscribe_meteo.py
#
# Simple subscriber to test meteo MQTT messages
#
import sys
import json
import signal
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from common.config import load_config
from common.mqtt_client import MQTTClient


def process_meteo_message(payload: str):
    """Process received meteo messages"""
    try:
        data = json.loads(payload)
        
        print(f"Received meteo update!")
        print(f"Total Stations: {data.get('count', 0)}")
        print(f"Timestamp: {data.get('timestamp')}\n")
        
        for station in data.get('stations', []):
            print(f"\n--- Station {station['station_id']} ---")
            print(f"Location: {station['location_name']}")
            print(f"Coordinates: ({station['location']['latitude']}, {station['location']['longitude']})")
            
            if station.get('measurement'):
                m = station['measurement']
                print(f"\nMeasurements:")
                print(f"  Temperature: {m['temperature']}°C")
                print(f"  Humidity: {m['humidity']}%")
                print(f"  Pressure: {m['pressure']} hPa")
                print(f"  Wind Intensity: {m['wind_intensity']} km/h")
                print(f"  Wind Direction: {m['wind_direction']}")
                print(f"  Radiation: {m['radiation']} W/m²")
                print(f"  Precipitation: {m['accumulated_precipitation']} mm")
                print(f"  Time: {m['time']}")
        
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON: {e}")
        print(f"Raw payload: {payload[:500]}")
    except Exception as e:
        print(f"Error processing message: {e}")


def main():
    print("Meteo MQTT Subscriber")
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
        client_id="meteo-subscriber-test",
    )
    
    # Connect and subscribe
    try:
        mqtt_client.connect()
        print(f"Connected to MQTT broker at {broker_host}:{config.broker_port}")
        
        mqtt_client.subscribe(config.meteo_updates_topic, process_meteo_message)
        print(f"Subscribed to topic: {config.meteo_updates_topic}")
        print("\nWaiting for messages...\n")
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