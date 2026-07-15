"""
Minimal traffic jam detection test with only 6 cars.

Tests pure traffic jam detection without other service interference.
- 1 lead car stops suddenly
- 5 cars behind slow down and form a traffic jam
- Verifies traffic jam is detected with 5+ cars below speed threshold
"""
import json
import os
import time
import subprocess
import sys
import uuid
from pathlib import Path
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed

import paho.mqtt.client as mqtt

SIM_DIR = Path(__file__).resolve().parent.parent.parent.parent / "simulations"
ROADS_DIR = SIM_DIR / "roads"

# Configuration
MQTT_HOST = os.getenv("TEST_MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("TEST_MQTT_PORT", "1884"))

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
    import queue
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


def test_traffic_jam(get_car_id):
    """
    Minimal traffic jam test with only 6 cars (minimum to trigger detection).
    
    Scenario:
    1. 5 cars traveling slowly on highway
    2. Lead car stops suddenly
    3. 5 cars behind slow down dramatically due to lead stopping
    4. Traffic jam should be detected (5+ cars below speed threshold)

    This is a simpler version compared to complex scenarios,
    testing only traffic jam detection without accident, overtaking, or emergency vehicle.
    """
    
    lead_car = get_car_id("minimal-jam-lead")
    jam_cars = [get_car_id(f"minimal-jam-car-{i}") for i in range(1, 6)]
    
    all_cars = [lead_car] + jam_cars
    
    for car in all_cars:
        ensure_car_exists(car)
    
    alert_topics = [
        "alerts/traffic_jam/+",
    ]
    
    with mqtt_alert_collector(alert_topics) as (client, alert_queue, all_alerts):
        
        # Load highway route
        with open(ROADS_DIR / "highway.json") as f:
            highway_coords = json.load(f)["features"][0]["geometry"]["coordinates"]
        
        # Phase 1: Cars moving
        phase1_iterations = 6
        for iteration in range(phase1_iterations):
            positions = []
            
            # Lead car moving
            lead_idx = iteration * 3 + 20
            if lead_idx < len(highway_coords):
                lat, lon = highway_coords[lead_idx]
                positions.append((lead_car, lat, lon))
            
            # Jam cars following with tight spacing
            for i, car in enumerate(jam_cars):
                car_idx = lead_idx - (i + 1) * 2  # 2 points spacing
                if 0 <= car_idx < len(highway_coords):
                    lat, lon = highway_coords[car_idx]
                    positions.append((car, lat, lon))
            
            send_positions_parallel(positions)
            time.sleep(0.08)
        
        # Lock lead car at accident location
        final_lead_idx = (phase1_iterations - 1) * 3 + 20
        accident_lat, accident_lon = highway_coords[final_lead_idx]
        
        # Phase 2: Cars stopped
        phase2_iterations = 8
        for iteration in range(phase2_iterations):
            positions = []
            
            # Lead car STOPPED
            positions.append((lead_car, accident_lat, accident_lon))
            
            # Jam cars STOPPED (maintain exact spacing from end of phase 1)
            for i, car in enumerate(jam_cars):
                car_idx = final_lead_idx - (i + 1) * 2
                if 0 <= car_idx < len(highway_coords):
                    lat, lon = highway_coords[car_idx]
                    positions.append((car, lat, lon))
            
            send_positions_parallel(positions)
            time.sleep(0.5)
    
    # Verify traffic jam was detected. Each jam now publishes once per
    # involved car under alerts/traffic_jam/{car_id}; deduplicate by jam_id.
    jam_alerts_dedup = {}
    for t, a in all_alerts:
        if a.get("alert_type") == "traffic_jam":
            jam_alerts_dedup[a.get("jam_id")] = a
    jam_alerts = list(jam_alerts_dedup.values())
    assert len(jam_alerts) > 0, (
        "Expected traffic jam detection with 5 slow/stopped cars. "
        f"Got {len(jam_alerts)} traffic jam alerts."
    )
    
    # Check jam severity (should be 5+ cars)
    if jam_alerts:
        max_severity = max(a.get("severity", 0) for a in jam_alerts)
        assert max_severity >= 5, (
            f"Expected traffic jam with 5+ cars, got severity={max_severity}"
        )
    
if __name__ == "__main__":
    def get_car_id(base_name: str) -> str:
        return f"{base_name}-{str(uuid.uuid4())[:8]}"
    test_traffic_jam(get_car_id)
