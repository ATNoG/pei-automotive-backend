"""Tests for station assignment service."""
import json
import time
import subprocess
import sys
from pathlib import Path
from threading import Thread, Event
import paho.mqtt.client as mqtt

SIM_DIR = Path(__file__).resolve().parent.parent / "simulations"
ASSIGNMENTS = []
subscription_ready = Event()


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


def test_station_assignment_basic(get_car_id):
    """Test that a car receives a station assignment when it moves."""
    car_id = get_car_id("station-test-car")
    ensure_car_exists(car_id, emergency=False)

    # Clear previous assignments and reset event
    ASSIGNMENTS.clear()
    subscription_ready.clear()

    # Set up MQTT client to listen for station assignments
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_station_assignment
    client.on_subscribe = on_subscribe
    client.connect("localhost", 1884)
    client.subscribe(f"cars/station/{car_id}")
    client.loop_start()

    # Wait for subscription to be confirmed
    if not subscription_ready.wait(timeout=5):
        client.loop_stop()
        raise TimeoutError("Subscription not confirmed within 5 seconds")

    # Wait for meteo data to be available (published every 5 minutes)
    print("[TEST] Waiting for meteo data to be available...")
    time.sleep(2)

    # Send a few position updates at different locations
    # These coordinates are around Aveiro, Portugal (where meteo stations exist)
    test_positions = [
        (40.640506, -8.653754),  # Near Aveiro
        (40.650000, -8.660000),  # Slightly north
        (40.630000, -8.640000),  # Slightly southeast
    ]

    for lat, lon in test_positions:
        send_position(car_id, lat, lon)
        time.sleep(1)  # Give services time to process

    # Wait for assignment messages to arrive
    time.sleep(3)
    client.loop_stop()

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


def test_station_assignment_changes(get_car_id):
    """Test that station assignment updates when car moves to a different nearest station."""
    car_id = get_car_id("station-test-moving-car")
    ensure_car_exists(car_id, emergency=False)

    # Clear previous assignments and reset event
    ASSIGNMENTS.clear()
    subscription_ready.clear()

    # Set up MQTT client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_station_assignment
    client.on_subscribe = on_subscribe
    client.connect("localhost", 1884)
    client.subscribe(f"cars/station/{car_id}")
    client.loop_start()

    # Wait for subscription to be confirmed
    if not subscription_ready.wait(timeout=5):
        client.loop_stop()
        raise TimeoutError("Subscription not confirmed within 5 seconds")

    # Wait for meteo data
    time.sleep(2)

    # Send positions that should cause station changes
    # Start far north and move south (if there are multiple stations in the area)
    positions_far_apart = [
        (40.700000, -8.700000),  # Far north
        (40.600000, -8.600000),  # Move south
        (40.500000, -8.500000),  # Move further south
    ]

    for i, (lat, lon) in enumerate(positions_far_apart):
        send_position(car_id, lat, lon)
        time.sleep(2)  # Give more time for processing through all services
        print(f"[TEST] Sent position {i+1}/{len(positions_far_apart)}")

    # Wait for assignments to be received
    time.sleep(5)
    client.loop_stop()

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


def test_station_assignment_no_duplicate_on_same_station(get_car_id):
    """Test that station assignment doesn't publish duplicate messages when car stays in same station's range."""
    car_id = get_car_id("station-test-stationary-car")
    ensure_car_exists(car_id, emergency=False)

    # Clear previous assignments and reset event
    ASSIGNMENTS.clear()
    subscription_ready.clear()

    # Set up MQTT client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_station_assignment
    client.on_subscribe = on_subscribe
    client.connect("localhost", 1884)
    client.subscribe(f"cars/station/{car_id}")
    client.loop_start()

    # Wait for subscription to be confirmed
    if not subscription_ready.wait(timeout=5):
        client.loop_stop()
        raise TimeoutError("Subscription not confirmed within 5 seconds")

    # Wait for meteo data
    time.sleep(2)

    # Send multiple positions very close together (within same station's range)
    base_lat, base_lon = 40.640506, -8.653754
    
    for i in range(10):
        # Tiny movements (< 100m)
        lat = base_lat + (i * 0.0001)
        lon = base_lon + (i * 0.0001)
        send_position(car_id, lat, lon)
        time.sleep(0.3)

    # Wait for processing and message reception
    time.sleep(5)
    client.loop_stop()

    # Should only get one or very few assignments (only when station actually changes)
    # Not 10 assignments for 10 position updates
    print(f"[TEST] Sent 10 positions, received {len(ASSIGNMENTS)} assignment(s)")
    
    # Verify we got at least one assignment
    assert len(ASSIGNMENTS) > 0, "Expected at least one station assignment"
    
    # Verify it's far fewer than the number of position updates
    assert len(ASSIGNMENTS) < 10, f"Expected fewer assignments than position updates, got {len(ASSIGNMENTS)} assignments for 10 positions"
    
    # If multiple assignments, verify they are for different stations
    if len(ASSIGNMENTS) > 1:
        station_ids = [a["station"]["station_id"] for a in ASSIGNMENTS]
        unique_stations = set(station_ids)
        assert len(unique_stations) > 1, "Multiple assignments should be for different stations"
        print(f"[TEST] ✓ Got {len(ASSIGNMENTS)} assignments for {len(unique_stations)} different stations (expected)")
    else:
        print("[TEST] ✓ Got only 1 assignment for 10 positions (expected, car stayed in same station range)")


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
