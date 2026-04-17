import json
import time

import paho.mqtt.client as mqtt

from helpers import (
    MQTT_HOST, MQTT_PORT, ROADS_DIR,
    ensure_car_exists, send_positions_parallel, standalone_get_car_id,
)

ALERTS = []


def on_message(client, userdata, msg):
    ALERTS.append(json.loads(msg.payload.decode()))


def _find_merge_index(highway_route, merge_lat, merge_lon):
    """find the index in highway_route closest to the merge point."""
    return min(
        range(len(highway_route)),
        key=lambda i: (highway_route[i][0] - merge_lat) ** 2 + (highway_route[i][1] - merge_lon) ** 2,
    )


def _simulate_merge_unsafe(highway_car, entering_car, highway_start_idx, highway_route, entering_route):
    """simulate both cars approaching the merge point over 10 steps - unsafe version"""
    for step in range(7):
        entering_idx = min((step * len(entering_route) // 8) + 2, len(entering_route) + 1)
        entering_lat, entering_lon = entering_route[entering_idx]

        highway_idx = highway_start_idx + step
        highway_idx = min(highway_idx, len(highway_route) - 1)
        highway_lat, highway_lon = highway_route[highway_idx]

        send_positions_parallel([
            (entering_car, entering_lat, entering_lon),
            (highway_car, highway_lat, highway_lon),
        ])
        time.sleep(0.1)

def _simulate_merge_safe(highway_car, entering_car, highway_start_idx, highway_route, entering_route):
    """simulate both cars approaching the merge point over 10 steps - safe version"""
    for step in range(7):
        entering_idx = min(step * len(entering_route) // 8, len(entering_route) - 1)
        entering_lat, entering_lon = entering_route[entering_idx]

        highway_idx = highway_start_idx + step
        highway_idx = min(highway_idx, len(highway_route) - 1)
        highway_lat, highway_lon = highway_route[highway_idx]

        send_positions_parallel([
            (entering_car, entering_lat, entering_lon),
            (highway_car, highway_lat, highway_lon),
        ])
        time.sleep(0.1)


def test_highway_entry_unsafe(get_car_id):
    ALERTS.clear()

    highway_car = get_car_id("highway-car")
    entering_car = get_car_id("entering-car")
    ensure_car_exists(highway_car)
    ensure_car_exists(entering_car)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe("alerts/highway_entry")
    client.loop_start()
    time.sleep(0.5)

    with open(ROADS_DIR / "highway.json") as f:
        highway_route = json.load(f)["features"][0]["geometry"]["coordinates"]
    with open(ROADS_DIR / "entering.json") as f:
        entering_route = json.load(f)["features"][0]["geometry"]["coordinates"]
    
    merge_lat, merge_lon = entering_route[-1]
    merge_idx = _find_merge_index(highway_route, merge_lat, merge_lon)
    # start close to merge point so predicted distance is well below threshold (~7m vs 15m)
    highway_start_idx = max(0, merge_idx - 7)

    _simulate_merge_unsafe(highway_car, entering_car, highway_start_idx, highway_route, entering_route)

    time.sleep(2)
    client.loop_stop()

    unsafe_alerts = [a for a in ALERTS if a.get("status") == "unsafe"]
    assert len(unsafe_alerts) > 0, f"expected unsafe alert but got: {ALERTS}"


def test_highway_entry_safe(get_car_id):
    ALERTS.clear()

    highway_car = get_car_id("highway-car-2")
    entering_car = get_car_id("entering-car-2")
    ensure_car_exists(highway_car)
    ensure_car_exists(entering_car)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe("alerts/highway_entry")
    client.loop_start()
    time.sleep(0.5)

    with open(ROADS_DIR / "highway.json") as f:
        highway_route = json.load(f)["features"][0]["geometry"]["coordinates"]
    
    with open(ROADS_DIR / "entering.json") as f:
        entering_route = json.load(f)["features"][0]["geometry"]["coordinates"]
    
    # Find merge point and position highway car to be within detection range but safely behind
    merge_lat, merge_lon = entering_route[-1]
    merge_idx = _find_merge_index(highway_route, merge_lat, merge_lon)
    # start farther from merge point so predicted distance is safely above threshold (~35-45m vs 15m)
    highway_start_idx = max(0, merge_idx - 11)

    _simulate_merge_safe(highway_car, entering_car, highway_start_idx, highway_route, entering_route)

    time.sleep(3)
    client.loop_stop()

    safe_alerts = [a for a in ALERTS if a.get("status") == "safe"]
    assert len(safe_alerts) > 0, f"expected safe alert but got: {ALERTS}"

if __name__ == "__main__":
    test_highway_entry_unsafe(standalone_get_car_id)
    test_highway_entry_safe(standalone_get_car_id)
