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
    if topic.startswith("alerts/overtaking/"):
        OVERTAKING_ALERTS.append(payload)
    elif topic.startswith("alerts/lane_merge/"):
        LANE_MERGE_ALERTS.append(payload)


def test_real_world_overtaking_direct(get_car_id):
    """
    Two-phase scenario on IT-campus roads (Aveiro). Positions go directly to
    Ditto (bypassing Hono), making the test ~10x faster than the Hono version
    and allowing 1-pt step sizes that produce realistic speeds.

    Phase 1 - Safe lane merge:
      car_left waits stopped at main_left[0] (speed=0, heading=None).
      car_entering walks every point of the entering ramp toward the merge
      point. The lane-merge detector skips car_left (speed=0) and fires
      lane_merge_safe for car_entering.

    Phase 2 - Overtaking:
      car_left accelerates onto main_left at ~2 m/iter (1 pt/iter, 2 m/pt).
      car_entering moves at ~1 m/iter on main_right and starts 5 indices ahead.
      left covers ~2 m/iter vs entering ~1 m/iter; they are level at i=5
      (~10 m each) and left pulls ahead → sign flips → overtaking alert fires.
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
    client.loop_start()
    time.sleep(0.1)
    client.subscribe("alerts/overtaking/+", qos=1)
    client.subscribe(f"alerts/lane_merge/{car_entering}", qos=1)
    time.sleep(0.1)

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

    # Phase 1: car_entering walks every point of the ramp; car_left is stopped
    # at the far end of its approach road (~16 m from the merge point), clearly
    # behind and to the left of the merge zone, yielding to the merging car.
    left_stopped_lat, left_stopped_lon = left_route[0]

    send_position_ditto(car_left, left_stopped_lat, left_stopped_lon)

    for e_lat, e_lon in entering_route:
        _send_pair(e_lat, e_lon, left_stopped_lat, left_stopped_lon)
        time.sleep(0.1)

    # Wait for the lane_merge_safe alert to propagate before Phase 2.
    time.sleep(1)

    # Transition: car_left walks left_route toward main_left while car_entering
    # moves slowly along main_right (1 pt/step ≈ 1 m/update). enter_idx is
    # carried into Phase 2 so car_entering never jumps position.
    enter_idx = 0
    for l_lat, l_lon in left_route[1:]:
        e_lat, e_lon = main_right[min(enter_idx, len(main_right) - 1)]
        _send_pair(e_lat, e_lon, l_lat, l_lon)
        enter_idx += 1
        time.sleep(0.1)

    # Phase 2: car_left accelerates onto main_left at step=2 (~4 m/update).
    # car_entering continues at ~1 m/update and is ~14 m ahead at phase start;
    # car_left closes ~3 m/update and overtakes around step 5.
    for i in range(len(main_left) // 2 + 1):
        l_idx = min(i * 2, len(main_left) - 1)
        e_lat, e_lon = main_right[min(enter_idx, len(main_right) - 1)]
        _send_pair(e_lat, e_lon, main_left[l_idx][0], main_left[l_idx][1])
        enter_idx += 1
        time.sleep(0.1)

    # Ditto WS throttling delays event delivery; poll until both alerts arrive.
    deadline = time.time() + 15
    while time.time() < deadline and (not OVERTAKING_ALERTS or not LANE_MERGE_ALERTS):
        time.sleep(0.1)
    client.loop_stop()

    assert len(LANE_MERGE_ALERTS) > 0, \
        f"expected at least one lane merge alert, got {len(LANE_MERGE_ALERTS)}"
    assert any(a.get("status") == "safe" for a in LANE_MERGE_ALERTS), \
        f"expected a safe lane merge; got statuses: {[a.get('status') for a in LANE_MERGE_ALERTS]}"
    assert len(OVERTAKING_ALERTS) > 0, \
        f"expected at least one overtaking alert, got {len(OVERTAKING_ALERTS)}"


if __name__ == "__main__":
    test_real_world_overtaking_direct(standalone_get_car_id)
