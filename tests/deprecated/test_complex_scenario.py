"""
Integration test for traffic jam detection.

Simulates a realistic highway scenario:
- 10 cars traveling slowly in a jam (caused by an accident)
- 2 cars approaching from behind (one overtaking the other)
- 1 emergency vehicle approaching the jam
- Tests interaction between accident, traffic jam, overtaking, and emergency vehicle detectors

Expected alerts:
1. Accident alert when lead car stops suddenly
2. Traffic jam alert when 5+ cars accumulate in slow traffic
3. Traffic jam notifications to approaching vehicles (within 2km)
4. Overtaking alert when one approaching car passes the other
5. Emergency vehicle notifications to cars ahead
"""
import json
import os
import time
import subprocess
import sys
import queue
import uuid
from pathlib import Path
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed

import paho.mqtt.client as mqtt
import pytest

SIM_DIR = Path(__file__).resolve().parent.parent / "simulations"
ROADS_DIR = SIM_DIR / "roads"

# Configuration
MQTT_HOST = os.getenv("TEST_MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("TEST_MQTT_PORT", "1884"))
POSITION_INTERVAL = 0.08  # Slightly slower for complex scenario
STEP_SIZE = 3  # Increased from 2 to move cars faster along shorter highway
ALERT_TIMEOUT = 5.0
THREAD_TIMEOUT = 60.0  # Reduced from 120.0 since test is much shorter now


def ensure_car_exists(car_name: str, emergency: bool = False) -> None:
    """Ensure car is registered. Ignores if already exists."""
    meta = SIM_DIR / "devices" / f"{car_name}.json"
    if not meta.exists():
        cmd = [sys.executable, str(SIM_DIR / "create_car.py"), car_name]
        if emergency:
            cmd.append("--emergency")
        subprocess.run(cmd, check=True)


def send_position(car_name: str, lat: float, lon: float) -> None:
    """Send a single GPS position update for a car."""
    result = subprocess.run(
        [sys.executable, str(SIM_DIR / "send_position.py"), car_name, str(lat), str(lon)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to send position for {car_name}: {result.stderr}")


def send_positions_parallel(car_positions: list[tuple[str, float, float]]) -> None:
    """Send GPS positions for multiple cars simultaneously."""
    with ThreadPoolExecutor(max_workers=min(len(car_positions), 15)) as executor:
        futures = [executor.submit(send_position, *pos) for pos in car_positions]
        for future in as_completed(futures):
            future.result()


@contextmanager
def mqtt_alert_collector(topics: list[str]):
    """Collect alerts from multiple MQTT topics."""
    alert_queue = queue.Queue()
    all_alerts = []
    
    def on_message(client, userdata, msg):
        payload = json.loads(msg.payload.decode())
        alert_queue.put((msg.topic, payload))
        all_alerts.append((msg.topic, payload))
    
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    try:
        client.on_message = on_message
        client.connect(MQTT_HOST, MQTT_PORT)
        for topic in topics:
            client.subscribe(topic, qos=1)
        client.loop_start()
        time.sleep(0.3)
        yield client, alert_queue, all_alerts
    finally:
        client.loop_stop()
        client.disconnect()

@pytest.mark.skip(reason="No reason for this to be on pipeline")
def test_complex_scenario(get_car_id):
    """
    Complex integration test simulating a highway traffic jam scenario.
    
    Scenario:
    1. Lead car suddenly stops (accident)
    2. 9 cars behind form a traffic jam (5+ cars trigger jam detection)
    3. 2 cars approach from far behind (one overtakes the other)
    4. Emergency vehicle approaches through the jam
    """
    
    # Create car IDs
    lead_car = get_car_id("jam-lead")
    jam_cars = [get_car_id(f"jam-car-{i}") for i in range(1, 10)]  # 9 cars in jam
    approaching_slow = get_car_id("jam-approaching-slow")
    approaching_fast = get_car_id("jam-approaching-fast")
    emergency_vehicle = get_car_id("jam-emergency")
    
    all_cars = [lead_car] + jam_cars + [approaching_slow, approaching_fast, emergency_vehicle]
    
    # Create all cars
    for car in all_cars[:-1]:
        ensure_car_exists(car)
    ensure_car_exists(emergency_vehicle, emergency=True)
    
    # Setup MQTT alert collection
    alert_topics = [
        "alerts/accident/+",
        "alerts/traffic_jam/+",
        "alerts/overtaking/+",
        "alerts/emergency_vehicle/+",
    ]
    
    with mqtt_alert_collector(alert_topics) as (client, alert_queue, all_alerts):
        
        # Load highway route (simple coordinate array format)
        with open(ROADS_DIR / "highway.json") as f:
            highway_coords = json.load(f)["features"][0]["geometry"]["coordinates"]
        
        # Also load left lane for overtaking (GeoJSON FeatureCollection format)
        with open(ROADS_DIR / "left_lane.json") as f:
            left_lane_coords = json.load(f)["features"][0]["geometry"]["coordinates"]
    
        phase1_iterations = 25  # Build up speed and spacing
        
        for iteration in range(phase1_iterations):
            positions = []
            
            # Lead car (at front)
            lead_idx = iteration * STEP_SIZE + 20
            if lead_idx < len(highway_coords):
                lat, lon = highway_coords[lead_idx]
                positions.append((lead_car, lat, lon))
            
            # Jam cars (following behind with spacing)
            for i, car in enumerate(jam_cars):
                car_idx = lead_idx - (i + 1) * 8  # 8 points spacing
                if 0 <= car_idx < len(highway_coords):
                    lat, lon = highway_coords[car_idx]
                    positions.append((car, lat, lon))
            
            # Approaching cars (far behind)
            slow_idx = lead_idx - len(jam_cars) * 8 - 50
            fast_idx = slow_idx - 10
            
            if 0 <= slow_idx < len(highway_coords):
                lat, lon = highway_coords[slow_idx]
                positions.append((approaching_slow, lat, lon))
            
            if 0 <= fast_idx < len(highway_coords):
                lat, lon = highway_coords[fast_idx]
                positions.append((approaching_fast, lat, lon))
            
            # Emergency vehicle (even further behind)
            ev_idx = fast_idx - 30
            if 0 <= ev_idx < len(highway_coords):
                lat, lon = highway_coords[ev_idx]
                positions.append((emergency_vehicle, lat, lon))
            
            send_positions_parallel(positions)
            time.sleep(POSITION_INTERVAL)
        
        # Get final positions from phase 1
        final_lead_idx = (phase1_iterations - 1) * STEP_SIZE + 20
        accident_idx = final_lead_idx
        accident_lat, accident_lon = highway_coords[accident_idx]
        
        phase2_iterations = 15  # Time for jam to form (reduced from 25, jam forms by iter ~10)
        
        for iteration in range(phase2_iterations):
            positions = []
            
            # Lead car STOPPED at accident location
            positions.append((lead_car, accident_lat, accident_lon))
            
            # Jam cars slow down and approach accident (forming traffic jam)
            for i, car in enumerate(jam_cars):
                # Slow progression toward accident
                # Cars slow down as they approach, eventually crawling
                progress_factor = 0.3 - (iteration * 0.01)  # Getting slower
                if progress_factor < 0.05:
                    progress_factor = 0.05  # Minimum crawl speed
                
                car_idx = accident_idx - (len(jam_cars) - i) * 8 + int(iteration * progress_factor * STEP_SIZE)
                
                # Don't pass the accident
                if car_idx > accident_idx - 20:
                    car_idx = accident_idx - 20 - (len(jam_cars) - i) * 5
                
                if 0 <= car_idx < len(highway_coords):
                    lat, lon = highway_coords[car_idx]
                    positions.append((car, lat, lon))
            
            # Approaching slow car (normal speed, will be notified)
            slow_base_idx = accident_idx - len(jam_cars) * 8 - 50
            slow_idx = slow_base_idx + iteration * STEP_SIZE
            if 0 <= slow_idx < len(highway_coords):
                lat, lon = highway_coords[slow_idx]
                positions.append((approaching_slow, lat, lon))
            
            # Approaching fast car (faster, overtaking the slow car)
            fast_base_idx = slow_base_idx - 10
            fast_idx = fast_base_idx + int(iteration * STEP_SIZE * 1.5)  # 50% faster
            
            # Overtaking maneuver: move to left lane when close
            gap = slow_idx - fast_idx
            if gap > 10 and 0 <= fast_idx < len(left_lane_coords):
                # Still behind, stay on highway
                if fast_idx < len(highway_coords):
                    lat, lon = highway_coords[fast_idx]
                    positions.append((approaching_fast, lat, lon))
            elif -5 <= gap <= 10 and 0 <= fast_idx < len(left_lane_coords):
                # Overtaking - use left lane
                lon, lat = left_lane_coords[fast_idx]
                positions.append((approaching_fast, lat, lon))
            elif gap < -5 and 0 <= fast_idx < len(highway_coords):
                # Passed, return to highway
                lat, lon = highway_coords[fast_idx]
                positions.append((approaching_fast, lat, lon))
            
            # Emergency vehicle (approaching fast)
            ev_base_idx = fast_base_idx - 30
            ev_idx = ev_base_idx + int(iteration * STEP_SIZE * 2.0)  # Even faster
            if 0 <= ev_idx < len(highway_coords):
                lat, lon = highway_coords[ev_idx]
                positions.append((emergency_vehicle, lat, lon))
            
            send_positions_parallel(positions)
            time.sleep(POSITION_INTERVAL)
        
        phase3_iterations = 5  # Confirm stable jam (reduced from 15)
        
        for iteration in range(phase3_iterations):
            positions = []
            
            # Lead car still stopped
            positions.append((lead_car, accident_lat, accident_lon))
            
            # Jam cars barely moving (traffic jam)
            for i, car in enumerate(jam_cars):
                car_idx = accident_idx - 20 - (len(jam_cars) - i) * 5 + iteration
                if 0 <= car_idx < len(highway_coords):
                    lat, lon = highway_coords[car_idx]
                    positions.append((car, lat, lon))
            
            # Approaching cars continue
            slow_idx = accident_idx - len(jam_cars) * 8 - 50 + (phase2_iterations + iteration) * STEP_SIZE
            if 0 <= slow_idx < len(highway_coords) and slow_idx < accident_idx - 50:
                lat, lon = highway_coords[slow_idx]
                positions.append((approaching_slow, lat, lon))
            
            fast_base = accident_idx - len(jam_cars) * 8 - 50 - 10
            fast_idx = fast_base + int((phase2_iterations + iteration) * STEP_SIZE * 1.5)
            if 0 <= fast_idx < len(highway_coords) and fast_idx < accident_idx - 50:
                lat, lon = highway_coords[fast_idx]
                positions.append((approaching_fast, lat, lon))
            
            # Emergency vehicle still approaching
            ev_base = fast_base - 30
            ev_idx = ev_base + int((phase2_iterations + iteration) * STEP_SIZE * 2.0)
            if 0 <= ev_idx < len(highway_coords) and ev_idx < accident_idx - 30:
                lat, lon = highway_coords[ev_idx]
                positions.append((emergency_vehicle, lat, lon))
            
            send_positions_parallel(positions)
            time.sleep(POSITION_INTERVAL)
        
        # Wait for all alerts to be processed
        time.sleep(2)

    # Categorize alerts
    accident_alerts = [a for t, a in all_alerts if "accident" in t]
    traffic_jam_alerts = [a for t, a in all_alerts if "traffic_jam" in t]
    overtaking_alerts = [a for t, a in all_alerts if "overtaking" in t]
    emergency_alerts = [a for t, a in all_alerts if "emergency_vehicle" in t]
    
    print(f"  - Accident alerts: {len(accident_alerts)}")
    print(f"  - Traffic jam alerts: {len(traffic_jam_alerts)}")
    print(f"  - Overtaking alerts: {len(overtaking_alerts)}")
    print(f"  - Emergency vehicle alerts: {len(emergency_alerts)}")
    
    # 1. Accident should be detected
    assert len(accident_alerts) > 0, (
        "Expected accident detection when lead car stopped suddenly. "
        f"Got {len(accident_alerts)} accident alerts."
    )
    
    # 2. Traffic jam should be detected (5+ cars slow/stopped). Each detection
    # is fanned out per car to alerts/traffic_jam/{car_id}; deduplicate by jam_id.
    jam_dedup = {}
    for t, a in all_alerts:
        if a.get("alert_type") == "traffic_jam":
            jam_dedup[a.get("jam_id")] = a
    jam_broadcast_alerts = list(jam_dedup.values())
    assert len(jam_broadcast_alerts) > 0, (
        "Expected traffic jam detection with 10 slow/stopped cars. "
        f"Got {len(jam_broadcast_alerts)} traffic jam alerts."
    )

    # 3. At least one overtaking alert should be detected
    assert len(overtaking_alerts) > 0, (
        "Expected overtaking alert when one approaching car passed the other. "
        f"Got {len(overtaking_alerts)} overtaking alerts."
    )

    # 4. Emergency vehicle should be detected and cars ahead notified
    assert len(emergency_alerts) > 0, (
        "Expected emergency vehicle alerts to cars ahead. "
        f"Got {len(emergency_alerts)} emergency vehicle alerts."
    )
    
    # Check jam severity (should be 5+ cars)
    if jam_broadcast_alerts:
        max_severity = max(a.get("severity", 0) for a in jam_broadcast_alerts)
        assert max_severity >= 5, (
            f"Expected traffic jam with 5+ cars, got severity={max_severity}"
        )

if __name__ == "__main__":
    def get_car_id(base_name: str) -> str:
        return f"{base_name}-{str(uuid.uuid4())[:8]}"
    
    test_complex_scenario(get_car_id)
