import json
import time
from threading import Thread

import paho.mqtt.client as mqtt

from helpers import (
    MQTT_HOST, MQTT_PORT, ROADS_DIR,
    ensure_car_exists, send_position_ditto, standalone_get_car_id,
)

OVERTAKING_ALERTS = []
LANE_MERGE_ALERTS = []


def on_message(client, userdata, msg):
    topic = msg.topic
    payload = json.loads(msg.payload.decode())
    if topic == "alerts/overtaking":
        OVERTAKING_ALERTS.append(payload)
    elif topic == "alerts/lane_merge":
        LANE_MERGE_ALERTS.append(payload)


def test_real_world_overtaking_direct(get_car_id):
    """
    Same real-world overtake scenario as test_real_world_overtaking, but
    positions are sent directly to Ditto (bypassing Hono), making the test
    ~10x faster and allowing 1-pt step sizes that produce realistic speeds.

    Sending directly to Ditto avoids:
      - subprocess + Python startup overhead
      - TLS MQTT handshake to Hono
      - 0.25 s post-connect sleep in send_position.py
      - final GET verification round-trip

    With Ditto HTTP latency ~50 ms each step covers ~1 m (main_right) or ~2 m
    (main_left) in ~70 ms → computed speed ~14-28 m/s (50-100 km/h).

    Sign-flip analysis (why the alert fires):
      - entering car starts at main_right[5] (~5 m past MAIN_START).
      - left car starts at main_left[0] (at MAIN_START).
      - main_right: 34 pts / ~33 m → ~0.97 m/pt.
      - main_left:  17 pts / ~33 m → ~1.94 m/pt.
      - Each iteration both cars advance 1 index.  Left covers ~2 m/iter,
        entering ~1 m/iter.  They are level at i=5 (~10 m each).
      - At i=5 the actual lat/lon geometry puts left ~0.2 m ahead → sign
        flips -1 → overtaking_event alert fires.
    """
    car_entering = get_car_id("rw-direct-entering")
    car_left = get_car_id("rw-direct-left")

    OVERTAKING_ALERTS.clear()
    LANE_MERGE_ALERTS.clear()

    ensure_car_exists(car_entering)
    ensure_car_exists(car_left)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe("alerts/overtaking")
    client.subscribe("alerts/lane_merge")
    client.loop_start()

    with open(ROADS_DIR / "real_world_entering.json") as f:
        entering_route = json.load(f)["features"][0]["geometry"]["coordinates"]
    with open(ROADS_DIR / "real_world_left.json") as f:
        left_route = json.load(f)["features"][0]["geometry"]["coordinates"]
    with open(ROADS_DIR / "real_world_main_right.json") as f:
        main_right = json.load(f)["features"][0]["geometry"]["coordinates"]
    with open(ROADS_DIR / "real_world_main_left.json") as f:
        main_left = json.load(f)["features"][0]["geometry"]["coordinates"]

    def _send_pair(e_lat, e_lon, l_lat, l_lon):
        t1 = Thread(target=send_position_ditto, args=(car_entering, e_lat, e_lon))
        t2 = Thread(target=send_position_ditto, args=(car_left, l_lat, l_lon))
        t1.start(); t2.start(); t1.join(); t2.join()

    # Phase 1: approach - step 2 on each route to establish heading.
    # Both routes are short (~18 m / ~15 m) so 9 iterations covers them fully.
    max_approach = max(len(entering_route), len(left_route))
    for i in range(0, max_approach, 2):
        e_lat, e_lon = entering_route[min(i, len(entering_route) - 1)]
        l_lat, l_lon = left_route[min(i, len(left_route) - 1)]
        _send_pair(e_lat, e_lon, l_lat, l_lon)
        time.sleep(0.02)

    # Phase 2+3: entering car on main_right (1 pt ≈ 1 m/iter),
    # left car on main_left (1 pt ≈ 2 m/iter).
    # entering starts at index 5 so the detector records sign +1 immediately.
    # Left overtakes entering at ~i=5 → sign flips to -1 → alert fires.
    for i in range(len(main_left)):
        e_idx = min(5 + i, len(main_right) - 1)
        e_lat, e_lon = main_right[e_idx]
        l_lat, l_lon = main_left[i]
        _send_pair(e_lat, e_lon, l_lat, l_lon)
        time.sleep(0.02)

    time.sleep(2)
    client.loop_stop()

    assert len(OVERTAKING_ALERTS) > 0, f"expected at least one overtaking alert, got {len(OVERTAKING_ALERTS)}"
    assert len(LANE_MERGE_ALERTS) > 0, f"expected at least one lane merge alert, got {len(LANE_MERGE_ALERTS)}"


if __name__ == "__main__":
    test_real_world_overtaking_direct(standalone_get_car_id)
