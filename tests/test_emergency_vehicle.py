import json
import time
from threading import Thread

import paho.mqtt.client as mqtt
import pytest

from helpers import (
    MQTT_HOST, MQTT_PORT, ROADS_DIR,
    ensure_car_exists, send_position_ditto, standalone_get_car_id,
)

ALERTS = []


def on_message(client, userdata, msg):
    ALERTS.append(json.loads(msg.payload.decode()))


def test_emergency_vehicle(get_car_id):
    car_regular = get_car_id("ev-test-regular")
    car_emergency = get_car_id("ev-test-emergency")

    ALERTS.clear()

    ensure_car_exists(car_regular, emergency=False)
    ensure_car_exists(car_emergency, emergency=True)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe("alerts/emergency_vehicle/+")
    client.loop_start()

    with open(ROADS_DIR / "right_lane.json") as f:
        right_lane = json.load(f)["features"][0]["geometry"]["coordinates"]
    with open(ROADS_DIR / "left_lane.json") as f:
        left_lane = json.load(f)["features"][0]["geometry"]["coordinates"]

    def _lerp(a, b, t):
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    def lane_pos(lane, idx):
        i = int(idx)
        t = idx - i
        if t < 0.001 or i + 1 >= len(lane):
            return lane[i]
        return _lerp(lane[i], lane[i + 1], t)

    i = 0.0
    step = 0.5
    limit = len(right_lane) - 51
    while i < limit:
        regular_idx = i + 4
        ev_idx = i * 1.6

        if int(regular_idx) >= len(right_lane) or int(ev_idx) >= len(right_lane):
            break

        r_lon, r_lat = lane_pos(right_lane, regular_idx)
        gap = regular_idx - ev_idx

        if gap > 1:
            e_lon, e_lat = lane_pos(right_lane, ev_idx)
        elif gap >= -1:
            t = (1 - gap) / 2
            r = lane_pos(right_lane, ev_idx)
            l = lane_pos(left_lane, ev_idx)
            e_lon = r[0] + (l[0] - r[0]) * t
            e_lat = r[1] + (l[1] - r[1]) * t
        elif gap > -9:
            e_lon, e_lat = lane_pos(left_lane, ev_idx)
        elif gap >= -11:
            t = (-9 - gap) / 2
            l = lane_pos(left_lane, ev_idx)
            r = lane_pos(right_lane, ev_idx)
            e_lon = l[0] + (r[0] - l[0]) * t
            e_lat = l[1] + (r[1] - l[1]) * t
        else:
            e_lon, e_lat = lane_pos(right_lane, ev_idx)

        t_regular = Thread(target=send_position_ditto, args=(car_regular, r_lat, r_lon))
        t_ev = Thread(target=send_position_ditto, args=(car_emergency, e_lat, e_lon))
        t_regular.start()
        t_ev.start()
        t_regular.join()
        t_ev.join()

        time.sleep(0.15)
        i += step

    time.sleep(1)
    client.loop_stop()

    assert len(ALERTS) > 0, "expected at least one emergency vehicle alert, got none"

    alert = ALERTS[0]
    assert alert["alert_type"] == "emergency_vehicle_nearby"
    assert alert["emergency_vehicle_id"] is not None
    assert alert["regular_car_id"] is not None
    assert alert["distance_m"] <= 500
    assert alert["direction"] in ("ahead", "behind", "nearby"), (
        f"expected direction in (ahead, behind, nearby), got {alert['direction']}"
    )


if __name__ == "__main__":
    test_emergency_vehicle(standalone_get_car_id)
