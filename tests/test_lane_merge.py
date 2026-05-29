import json
import time
from threading import Thread

import paho.mqtt.client as mqtt

from helpers import (
    MQTT_HOST, MQTT_PORT, ROADS_DIR,
    ensure_car_exists, send_position_ditto, standalone_get_car_id,
)

ALERTS = []


def on_message(client, userdata, msg):
    ALERTS.append(json.loads(msg.payload.decode()))


def _find_merge_index(highway_route, merge_lat, merge_lon):
    return min(
        range(len(highway_route)),
        key=lambda i: (highway_route[i][0] - merge_lat) ** 2 + (highway_route[i][1] - merge_lon) ** 2,
    )


def _send_pair(c1, lat1, lon1, c2, lat2, lon2):
    t1 = Thread(target=send_position_ditto, args=(c1, lat1, lon1))
    t2 = Thread(target=send_position_ditto, args=(c2, lat2, lon2))
    t1.start()
    t2.start()
    t1.join()
    t2.join()


def _simulate_merge_unsafe(highway_car, entering_car, highway_start_idx, highway_route, entering_route):
    for step in range(15):
        entering_idx = min((step * len(entering_route) // 16) + 1, len(entering_route) - 1)
        entering_lat, entering_lon = entering_route[entering_idx]

        highway_idx = highway_start_idx + step
        highway_idx = min(highway_idx, len(highway_route) - 1)
        highway_lat, highway_lon = highway_route[highway_idx]

        _send_pair(entering_car, entering_lat, entering_lon,
                   highway_car, highway_lat, highway_lon)
        time.sleep(0.08)


def _simulate_merge_safe(highway_car, entering_car, highway_start_idx, highway_route, entering_route):
    for step in range(15):
        entering_idx = min(step * len(entering_route) // 14, len(entering_route) - 1)
        entering_lat, entering_lon = entering_route[entering_idx]

        highway_idx = highway_start_idx + step
        highway_idx = min(highway_idx, len(highway_route) - 1)
        highway_lat, highway_lon = highway_route[highway_idx]

        _send_pair(entering_car, entering_lat, entering_lon,
                   highway_car, highway_lat, highway_lon)
        time.sleep(0.08)


def test_merge_unsafe(get_car_id):
    ALERTS.clear()

    highway_car = get_car_id("highway-car")
    entering_car = get_car_id("entering-car")
    ensure_car_exists(highway_car)
    ensure_car_exists(entering_car)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe("alerts/lane_merge/{car_id}".format(car_id=entering_car))
    client.loop_start()
    time.sleep(0.5)

    with open(ROADS_DIR / "highway.json") as f:
        highway_route = json.load(f)["features"][0]["geometry"]["coordinates"]
    with open(ROADS_DIR / "entering.json") as f:
        entering_route = json.load(f)["features"][0]["geometry"]["coordinates"]

    merge_lat, merge_lon = entering_route[-1]
    merge_idx = _find_merge_index(highway_route, merge_lat, merge_lon)
    highway_start_idx = max(0, merge_idx - 6)

    _simulate_merge_unsafe(highway_car, entering_car, highway_start_idx, highway_route, entering_route)

    time.sleep(2)
    client.loop_stop()

    unsafe_alerts = [a for a in ALERTS if a.get("status") == "unsafe"]
    assert len(unsafe_alerts) > 0, f"expected unsafe alert but got: {ALERTS}"


def test_merge_safe(get_car_id):
    ALERTS.clear()

    highway_car = get_car_id("highway-car-2")
    entering_car = get_car_id("entering-car-2")
    ensure_car_exists(highway_car)
    ensure_car_exists(entering_car)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe("alerts/lane_merge/{car_id}".format(car_id=entering_car))
    client.loop_start()
    time.sleep(0.5)

    with open(ROADS_DIR / "highway.json") as f:
        highway_route = json.load(f)["features"][0]["geometry"]["coordinates"]

    with open(ROADS_DIR / "entering.json") as f:
        entering_route = json.load(f)["features"][0]["geometry"]["coordinates"]

    merge_lat, merge_lon = entering_route[-1]
    merge_idx = _find_merge_index(highway_route, merge_lat, merge_lon)
    highway_start_idx = max(0, merge_idx - 11)

    _simulate_merge_safe(highway_car, entering_car, highway_start_idx, highway_route, entering_route)

    time.sleep(3)
    client.loop_stop()

    safe_alerts = [a for a in ALERTS if a.get("status") == "safe"]
    assert len(safe_alerts) > 0, f"expected safe alert but got: {ALERTS}"


if __name__ == "__main__":
    test_merge_unsafe(standalone_get_car_id)
    test_merge_safe(standalone_get_car_id)
