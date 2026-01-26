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
    highway_start_idx = min(
        range(len(highway_route)),
        key=lambda i: ((highway_route[i][0] - merge_lat)**2 + (highway_route[i][1] - merge_lon)**2)**0.5
    )

    for step in range(15):
        entering_idx = min((step * len(entering_route)) // 6, len(entering_route) - 1)
        entering_lat, entering_lon = entering_route[entering_idx]
        
        highway_idx = max(highway_start_idx - 5, 0) + step
        highway_idx = min(highway_idx, len(highway_route) - 1)
        highway_lat, highway_lon = highway_route[highway_idx]
        
        thread_entering = Thread(target=lambda: subprocess.run(
            [sys.executable, str(SIM_DIR / "send_position.py"), entering_car, str(entering_lat), str(entering_lon)],
            check=True,
        ))
        thread_highway = Thread(target=lambda: subprocess.run(
            [sys.executable, str(SIM_DIR / "send_position.py"), highway_car, str(highway_lat), str(highway_lon)],
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

    ALERTS.clear()
    
    highway_start_idx = 0

    for step in range(10):
        entering_idx = min(step * len(entering_route) // 10, len(entering_route) - 1)
        entering_lat, entering_lon = entering_route[entering_idx]
        
        highway_idx = highway_start_idx + step // 2
        highway_idx = min(highway_idx, len(highway_route) - 1)
        highway_lat, highway_lon = highway_route[highway_idx]
        
        thread_entering = Thread(target=lambda: subprocess.run(
            [sys.executable, str(SIM_DIR / "send_position.py"), entering_car, str(entering_lat), str(entering_lon)],
            check=True,
        ))
        thread_highway = Thread(target=lambda: subprocess.run(
            [sys.executable, str(SIM_DIR / "send_position.py"), highway_car, str(highway_lat), str(highway_lon)],
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

