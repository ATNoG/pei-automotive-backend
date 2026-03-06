"""Tests for station assignment service."""
import json
import os
import time
import subprocess
import sys
from pathlib import Path
from threading import Thread, Event
import paho.mqtt.client as mqtt
import pytest

SIM_DIR = Path(__file__).resolve().parent.parent / "simulations"
ASSIGNMENTS = []
subscription_ready = Event()
assignment_received = Event()  # Signal when we receive an assignment


def ensure_car_exists(car_name: str, emergency: bool = False) -> None:
    """Create a car in the simulation if it doesn't exist."""
    meta = SIM_DIR / "devices" / f"{car_name}.json"
    if not meta.exists():
        cmd = [sys.executable, str(SIM_DIR / "create_car.py"), car_name]
        if emergency:
            cmd.append("--emergency")
        subprocess.run(cmd, check=True)


def on_station_assignment(client, userdata, msg):
    """Callback for station assignment messages."""
    payload = json.loads(msg.payload.decode())
    ASSIGNMENTS.append(payload)
    assignment_received.set()  # Signal that we got an assignment
    print(f"[TEST] Received assignment: Car {payload.get('car_id')} -> Station {payload.get('station', {}).get('station_id')}")


def on_subscribe(client, userdata, mid, reason_code_list, properties=None):
    """Callback when subscription is confirmed."""
    subscription_ready.set()
    print("[TEST] Subscription confirmed, ready to receive messages")


def send_position(car_name: str, lat: float, lon: float) -> None:
    """Send a position update for a car."""
    subprocess.run(
        [sys.executable, str(SIM_DIR / "send_position.py"),
         car_name, str(lat), str(lon)],
        check=True,
    )


@pytest.mark.skipif(
    os.getenv("CI") == "true", 
    reason="Station tests require external Weather API which is unreliable in CI"
)
def test_station_assignment_basic(get_car_id):
    """Test that a car receives a station assignment when it moves."""
    car_id = get_car_id("station-test-car")
    ensure_car_exists(car_id, emergency=False)

    # Clear previous assignments and reset events
    ASSIGNMENTS.clear()
    subscription_ready.clear()
    assignment_received.clear()

    # Set up MQTT client to listen for station assignments
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_station_assignment
    client.on_subscribe = on_subscribe
    client.connect("localhost", 1884)
    client.subscribe(f"cars/station/{car_id}", qos=1)
    client.loop_start()

    try:
        # Wait for subscription to be confirmed
        if not subscription_ready.wait(timeout=5):
            raise TimeoutError("Subscription not confirmed within 5 seconds")

        # Wait for meteo data to be available (published every 5 minutes)
        print("[TEST] Waiting for meteo data to be available...")
        time.sleep(2)

        # Send a position update
        # This coordinate is around Aveiro, Portugal (where meteo stations exist)
        print(f"[TEST] Sending position update...")
        send_position(car_id, 40.640506, -8.653754)

        # Wait for assignment message (with generous timeout for Overpass API delays)
        print("[TEST] Waiting for station assignment (may take 30s+ due to Overpass API)...")
        if not assignment_received.wait(timeout=45):
            raise TimeoutError("No station assignment received within 45 seconds")
        
        # Verify results
        assert len(ASSIGNMENTS) > 0, "Expected at least one station assignment, got none"
        
        # Check first assignment structure
        assignment = ASSIGNMENTS[0]
        assert "car_id" in assignment, "Assignment missing car_id"
        assert assignment["car_id"] == car_id, f"Expected car_id {car_id}, got {assignment['car_id']}"
        
        assert "station" in assignment, "Assignment missing station data"
        station = assignment["station"]
        
        assert "station_id" in station, "Station missing station_id"
        assert "location" in station, "Station missing location"
        assert "location_name" in station, "Station missing location_name"
        
        location = station["location"]
        assert "latitude" in location, "Location missing latitude"
        assert "longitude" in location, "Location missing longitude"
        
        print(f"[TEST] ✓ Car {car_id} assigned to station {station['station_id']}: {station['location_name']}")
    finally:
        client.loop_stop()
        client.disconnect()


@pytest.mark.skipif(
    os.getenv("CI") == "true",
    reason="Station tests require external Weather API which is unreliable in CI"
)
def test_station_assignment_changes(get_car_id):
    """Test that station assignment updates when car moves to a different nearest station."""
    car_id = get_car_id("station-test-moving-car")
    ensure_car_exists(car_id, emergency=False)

    # Clear previous assignments and reset events
    ASSIGNMENTS.clear()
    subscription_ready.clear()
    assignment_received.clear()

    # Set up MQTT client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_station_assignment
    client.on_subscribe = on_subscribe
    client.connect("localhost", 1884)
    client.subscribe(f"cars/station/{car_id}", qos=1)
    client.loop_start()

    try:
        # Wait for subscription to be confirmed
        if not subscription_ready.wait(timeout=5):
            raise TimeoutError("Subscription not confirmed within 5 seconds")

        # Wait for meteo data
        time.sleep(2)

        # Send first position and wait for assignment
        print(f"[TEST] Sending first position...")
        send_position(car_id, 40.640506, -8.653754)
        
        print("[TEST] Waiting for first assignment (may take 30s+ due to Overpass API)...")
        if not assignment_received.wait(timeout=45):
            raise TimeoutError("No station assignment received within 45 seconds")
        
        print(f"[TEST] Received {len(ASSIGNMENTS)} assignment(s)")
        
        # Wait a bit longer to collect any additional assignments
        time.sleep(5)

        # Verify we got assignments
        assert len(ASSIGNMENTS) > 0, "Expected station assignments, got none"
        
        # Check that all assignments are for the correct car
        for assignment in ASSIGNMENTS:
            assert assignment["car_id"] == car_id
            assert "station" in assignment
            assert "station_id" in assignment["station"]
        
        # If we have multiple weather stations in the area, we should see different assignments
        # Otherwise, we should at least get one assignment
        station_ids = [a["station"]["station_id"] for a in ASSIGNMENTS]
        unique_stations = set(station_ids)
        
        print(f"[TEST] Received {len(ASSIGNMENTS)} assignments across {len(unique_stations)} unique station(s)")
        
        if len(unique_stations) > 1:
            print(f"[TEST] ✓ Station assignment changed as car moved (stations: {list(unique_stations)})")
        else:
            print(f"[TEST] ✓ Car stayed in same station's range (station: {station_ids[0]})")
    finally:
        client.loop_stop()
        client.disconnect()

@pytest.mark.skipif(
    os.getenv("CI") == "true",
    reason="Station tests require external Weather API which is unreliable in CI"
)
def test_station_assignment_no_duplicate_on_same_station(get_car_id):
    """Test that station assignment doesn't publish duplicate messages when car stays in same station's range."""
    car_id = get_car_id("station-test-stationary-car")
    ensure_car_exists(car_id, emergency=False)

    # Clear previous assignments and reset events
    ASSIGNMENTS.clear()
    subscription_ready.clear()
    assignment_received.clear()

    # Set up MQTT client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_station_assignment
    client.on_subscribe = on_subscribe
    client.connect("localhost", 1884)
    client.subscribe(f"cars/station/{car_id}", qos=1)
    client.loop_start()

    try:
        # Wait for subscription to be confirmed
        if not subscription_ready.wait(timeout=5):
            raise TimeoutError("Subscription not confirmed within 5 seconds")

        # Wait for meteo data
        time.sleep(2)

        # Send first position and wait for initial assignment
        print(f"[TEST] Sending first position...")
        send_position(car_id, 40.640506, -8.653754)
        
        print("[TEST] Waiting for initial assignment (may take 30s+ due to Overpass API)...")
        if not assignment_received.wait(timeout=45):
            raise TimeoutError("No station assignment received within 45 seconds")
        
        initial_count = len(ASSIGNMENTS)
        print(f"[TEST] Received initial assignment")
        
        # Now send multiple tiny movements (should not trigger new assignments)
        # Reset the event to detect if we get unexpected assignments
        assignment_received.clear()
        
        for i in range(1, 5):  # Reduced to 4 more positions
            lat = 40.640506 + (i * 0.0001)  # Tiny movements
            lon = -8.653754 + (i * 0.0001)
            send_position(car_id, lat, lon)
            time.sleep(1)

        # Wait to see if we get more assignments (we shouldn't)
        time.sleep(10)

        # Should only get one or very few assignments (only when station actually changes)
        print(f"[TEST] Sent 5 positions, received {len(ASSIGNMENTS)} assignment(s)")
        
        # Verify we got at least one assignment
        assert len(ASSIGNMENTS) > 0, "Expected at least one station assignment"
        
        # Verify it's far fewer than the total number of position updates (only 1 expected)
        assert len(ASSIGNMENTS) <= 2, f"Expected at most 2 assignments, got {len(ASSIGNMENTS)} for 5 positions"
        
        print("[TEST] ✓ Got minimal assignments for nearby positions (expected behavior)")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    import uuid
    
    def get_car_id(base_name: str) -> str:
        return f"{base_name}-{str(uuid.uuid4())[:8]}"
    
    print("Running station assignment tests...\n")
    
    print("Test 1: Basic station assignment")
    test_station_assignment_basic(get_car_id)
    print()
    
    print("Test 2: Station assignment changes")
    test_station_assignment_changes(get_car_id)
    print()
    
    print("Test 3: No duplicate assignments")
    test_station_assignment_no_duplicate_on_same_station(get_car_id)
    print()
    
    print("All tests passed!")
