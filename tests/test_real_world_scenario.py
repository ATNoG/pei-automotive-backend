import json
import time
from threading import Thread

import paho.mqtt.client as mqtt

from helpers import (
    MQTT_HOST, MQTT_PORT, ROADS_DIR,
    ensure_car_exists, send_position, standalone_get_car_id,
)

OVERTAKING_ALERTS = []
LANE_MERGE_ALERTS = []


def on_message(client, userdata, msg):
    topic = msg.topic
    payload = json.loads(msg.payload.decode())
    if topic.startswith("alerts/overtaking/"):
        OVERTAKING_ALERTS.append(payload)
    elif topic.startswith("alerts/lane_merge/"):
        LANE_MERGE_ALERTS.append(payload)


def test_real_world_overtaking(get_car_id):
    """
    Real-world scenario on IT-campus roads (Aveiro, ~40.634N 8.660W).

    Note: the frontend map may show these vehicles on a footpath/access road
    that is not rendered as a drivable road in OSM - this is expected for this
    location and does not affect test correctness.

    Flow:
      1. Both cars approach their respective lanes (entering ramp + left lane).
      2. entering car enters main_right 5 indices ahead of left car entering
         main_left - the detector records relative sign +1 (entering is ahead).
      3. Left car advances 6 m/step (step=3 on main_left, 17 pts / 33 m) while
         entering car advances 5 m/step (step=5 on main_right, 34 pts / 33 m).
         Left car overtakes at ~step 4 → sign flips to -1 → alert fires.

    Step sizes are chosen so each update jumps ~5-6 m. Given Hono HTTP latency
    of ~0.5-1 s per update this yields computed speeds of 25-40 km/h, avoiding
    the near-zero speed produced by sending points only 1 m apart.
    """
    car_entering = get_car_id("rw-entering-car")
    car_left = get_car_id("rw-left-car")

    OVERTAKING_ALERTS.clear()
    LANE_MERGE_ALERTS.clear()

    ensure_car_exists(car_entering)
    ensure_car_exists(car_left)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe("alerts/overtaking/+")
    client.subscribe("alerts/lane_merge/{car_id}".format(car_id=car_entering))
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
        t1 = Thread(target=send_position, args=(car_entering, e_lat, e_lon))
        t2 = Thread(target=send_position, args=(car_left, l_lat, l_lon))
        t1.start(); t2.start(); t1.join(); t2.join()

    # Phase 1: approach - step size 4 to establish heading for both cars.
    max_approach = max(len(entering_route), len(left_route))
    for i in range(0, max_approach, 4):
        e_lat, e_lon = entering_route[min(i, len(entering_route) - 1)]
        l_lat, l_lon = left_route[min(i, len(left_route) - 1)]
        _send_pair(e_lat, e_lon, l_lat, l_lon)
        time.sleep(0.05)

    # Phase 2+3: entering car on main_right (step 5 ≈ 5 m/update),
    # left car on main_left (step 3 ≈ 6 m/update).
    # entering car starts at index 5 so it is clearly ahead from the first
    # iteration → initial sign = +1.  Left car overtakes around iteration 4.
    ENTER_STEP = 5
    LEFT_STEP = 3
    for i in range(8):
        e_idx = min(5 + i * ENTER_STEP, len(main_right) - 1)
        l_idx = min(i * LEFT_STEP, len(main_left) - 1)
        e_lat, e_lon = main_right[e_idx]
        l_lat, l_lon = main_left[l_idx]
        _send_pair(e_lat, e_lon, l_lat, l_lon)
        time.sleep(0.05)

    time.sleep(2)
    client.loop_stop()

    assert len(OVERTAKING_ALERTS) > 0, f"expected at least one overtaking alert, got {len(OVERTAKING_ALERTS)}"
    assert len(LANE_MERGE_ALERTS) > 0, f"expected at least one lane merge alert, got {len(LANE_MERGE_ALERTS)}"


if __name__ == "__main__":
    test_real_world_overtaking(standalone_get_car_id)
