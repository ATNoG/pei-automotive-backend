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
    Two-phase scenario on IT-campus roads (Aveiro, ~40.634N 8.660W).

    Phase 1 - Safe lane merge:
      car_left waits stopped at the entrance of the main left lane (speed=0,
      heading=None). car_entering walks the ramp toward the merge point.
      The lane-merge detector skips car_left (speed=0) and fires lane_merge_safe.

    Phase 2 - Overtaking:
      car_left accelerates onto main_left at ~6 m/update (step=3, 2 m/pt).
      car_entering moves at ~5 m/update (step=5, 1 m/pt) and starts 5 indices
      ahead. car_left gains ~1 m/update and overtakes at around step 5,
      flipping the relative sign and triggering an overtaking alert.
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
    client.loop_start()
    time.sleep(0.05)
    client.subscribe("alerts/overtaking/+", qos=1)
    client.subscribe(f"alerts/lane_merge/{car_entering}", qos=1)
    time.sleep(0.05)

    with open(ROADS_DIR / "real_world_entering.json") as f:
        entering_route = json.load(f)["features"][0]["geometry"]["coordinates"]
    with open(ROADS_DIR / "real_world_main_right.json") as f:
        main_right = json.load(f)["features"][0]["geometry"]["coordinates"]
    with open(ROADS_DIR / "real_world_main_left.json") as f:
        main_left = json.load(f)["features"][0]["geometry"]["coordinates"]

    # Phase 1: car_entering drives the ramp; car_left is stopped at the start
    # of the main left lane, yielding to the merging car.
    left_stopped_lat, left_stopped_lon = main_left[0]

    # Establish car_left's initial GPS state (no speed on first update).
    send_position(car_left, left_stopped_lat, left_stopped_lon)

    # car_entering walks the entering ramp at step=4 while car_left stays put.
    # Sending the same position repeatedly makes position_processor compute
    # speed=0 and heading=None for car_left, which the lane-merge detector
    # treats as a yielding car and skips when deciding safety.
    for i in range(0, len(entering_route), 4):
        e_lat, e_lon = entering_route[i]
        t1 = Thread(target=send_position, args=(car_entering, e_lat, e_lon))
        t2 = Thread(target=send_position, args=(car_left, left_stopped_lat, left_stopped_lon))
        t1.start(); t2.start(); t1.join(); t2.join()
        time.sleep(0.05)

    # Wait for the lane_merge_safe alert to propagate before starting Phase 2.
    time.sleep(2)

    # Phase 2: car_left accelerates and overtakes car_entering.
    # car_entering starts at main_right[5] (~5 m ahead) and advances 5 pts/step.
    # car_left starts at main_left[0] and advances 3 pts/step (~6 m/update).
    # Gap closes by ~1 m/update; sign flips around step 5.
    ENTER_STEP = 5
    LEFT_STEP = 3
    for i in range(8):
        e_idx = min(5 + i * ENTER_STEP, len(main_right) - 1)
        l_idx = min(i * LEFT_STEP, len(main_left) - 1)
        e_lat, e_lon = main_right[e_idx]
        l_lat, l_lon = main_left[l_idx]
        t1 = Thread(target=send_position, args=(car_entering, e_lat, e_lon))
        t2 = Thread(target=send_position, args=(car_left, l_lat, l_lon))
        t1.start(); t2.start(); t1.join(); t2.join()
        time.sleep(0.05)

    time.sleep(2)
    client.loop_stop()

    assert len(LANE_MERGE_ALERTS) > 0, \
        f"expected at least one lane merge alert, got {len(LANE_MERGE_ALERTS)}"
    assert any(a.get("status") == "safe" for a in LANE_MERGE_ALERTS), \
        f"expected a safe lane merge; got statuses: {[a.get('status') for a in LANE_MERGE_ALERTS]}"
    assert len(OVERTAKING_ALERTS) > 0, \
        f"expected at least one overtaking alert, got {len(OVERTAKING_ALERTS)}"


if __name__ == "__main__":
    test_real_world_overtaking(standalone_get_car_id)
