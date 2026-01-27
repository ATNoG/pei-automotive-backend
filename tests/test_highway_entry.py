import json
import time
import subprocess
import sys
from pathlib import Path
from threading import Thread

import paho.mqtt.client as mqtt

SIM_DIR = Path(__file__).resolve().parent.parent / "simulations"
ROADS_DIR = Path(__file__).resolve().parent.parent / "simulations/roads"
ALERTS = []


def ensure_car_exists(car_name: str) -> None:
    meta = SIM_DIR / "devices" / f"{car_name}.json"
    if not meta.exists():
        subprocess.run(
            [sys.executable, str(SIM_DIR / "create_car.py"), car_name],
            check=True,
        )


def on_highway_entry_alert(client, userdata, msg):
    ALERTS.append(json.loads(msg.payload.decode()))


def test_highway_entry_unsafe():
    highway_car = "highway-car"
    entering_car = "entering-car"
    ensure_car_exists(highway_car)
    ensure_car_exists(entering_car)

    # subscribe to speed alerts
    client = mqtt.Client()
    client.connect("localhost", 1884)
    client.subscribe("alerts/highway_entry")
    client.on_message = on_highway_entry_alert
    client.loop_start()

    # load coordinates
    with open(ROADS_DIR / "highway.json") as f:
        highway_route = json.load(f)
    with open(ROADS_DIR / "entering.json") as f:
        entering_route = json.load(f)

    ALERTS.clear()
    
    merge_lat, merge_lon = entering_route[-1]
    # Find the highway point closest to merge point
    merge_idx = min(
        range(len(highway_route)),
        key=lambda i: ((highway_route[i][0] - merge_lat)**2 + (highway_route[i][1] - merge_lon)**2)**0.5
    )
    # Start highway car closer to merge point to create collision scenario
    # Both cars should arrive at merge point at approximately the same time
    highway_start_idx = max(0, merge_idx - 8)

    for step in range(10):
        # Entering car progresses through full route
        entering_idx = min(step * len(entering_route) // 8, len(entering_route) - 1)
        entering_lat, entering_lon = entering_route[entering_idx]
        
        # Highway car moves at same pace, both converging on merge point
        highway_idx = highway_start_idx + step
        highway_idx = min(highway_idx, len(highway_route) - 1)
        highway_lat, highway_lon = highway_route[highway_idx]
        
        thread_entering = Thread(target=lambda c=entering_car, lat=entering_lat, lon=entering_lon: subprocess.run(
            [sys.executable, str(SIM_DIR / "send_position.py"), c, str(lat), str(lon)],
            check=True,
        ))
        thread_highway = Thread(target=lambda c=highway_car, lat=highway_lat, lon=highway_lon: subprocess.run(
            [sys.executable, str(SIM_DIR / "send_position.py"), c, str(lat), str(lon)],
            check=True,
        ))
        
        thread_entering.start()
        thread_highway.start()
        thread_entering.join()
        thread_highway.join()
        
        time.sleep(0.01)

    time.sleep(1)
    client.loop_stop()

    unsafe_alerts = [a for a in ALERTS if a.get("status") == "unsafe"]
    assert len(unsafe_alerts) > 0, f"Expected unsafe alert but got: {ALERTS}"


def test_highway_entry_safe():
    ALERTS.clear()
    
    highway_car = "highway-car-2"
    entering_car = "entering-car-2"
    
    ensure_car_exists(highway_car)
    ensure_car_exists(entering_car)

    client = mqtt.Client()
    client.connect("localhost", 1884)
    client.subscribe("alerts/highway_entry")
    client.on_message = on_highway_entry_alert
    client.loop_start()

    with open(ROADS_DIR / "highway.json") as f:
        highway_route = json.load(f)
    
    with open(ROADS_DIR / "entering.json") as f:
        entering_route = json.load(f)
    
    # Start highway car at the beginning - far from merge point
    # It moves slowly so the entering car merges well before it arrives
    highway_start_idx = 0

    for step in range(10):
        entering_idx = min(step * len(entering_route) // 10, len(entering_route) - 1)
        entering_lat, entering_lon = entering_route[entering_idx]
        
        # Highway car moves slowly (step // 3) so it stays far from merge
        highway_idx = highway_start_idx + step // 3
        highway_idx = min(highway_idx, len(highway_route) - 1)
        highway_lat, highway_lon = highway_route[highway_idx]
        
        thread_entering = Thread(target=lambda c=entering_car, lat=entering_lat, lon=entering_lon: subprocess.run(
            [sys.executable, str(SIM_DIR / "send_position.py"), c, str(lat), str(lon)],
            check=True,
        ))
        thread_highway = Thread(target=lambda c=highway_car, lat=highway_lat, lon=highway_lon: subprocess.run(
            [sys.executable, str(SIM_DIR / "send_position.py"), c, str(lat), str(lon)],
            check=True,
        ))
        
        thread_entering.start()
        thread_highway.start()
        thread_entering.join()
        thread_highway.join()
        
        time.sleep(0.01)

    time.sleep(1)
    client.loop_stop()

    safe_alerts = [a for a in ALERTS if a.get("status") == "safe"]
    assert len(safe_alerts) > 0, f"Expected safe alert but got: {ALERTS}"


if __name__ == "__main__":
    test_highway_entry_unsafe()
    test_highway_entry_safe()

