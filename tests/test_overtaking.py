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


def test_overtaking(get_car_id):
    car_slow = get_car_id("overtaking-car-front")
    car_fast = get_car_id("overtaking-car-behind")

    ALERTS.clear()

    ensure_car_exists(car_slow)
    ensure_car_exists(car_fast)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe("alerts/overtaking/+")
    client.loop_start()

    with open(ROADS_DIR / "right_lane.json") as f:
        right_lane = json.load(f)["features"][0]["geometry"]["coordinates"]
    with open(ROADS_DIR / "left_lane.json") as f:
        left_lane = json.load(f)["features"][0]["geometry"]["coordinates"]

    for i in range(0, len(right_lane) - 51, 1):
        slow_idx = i + 4
        fast_idx = round(i * 1.6)

        if slow_idx >= len(right_lane) or fast_idx >= len(right_lane):
            break

        s_lon, s_lat = right_lane[slow_idx]
        gap = slow_idx - fast_idx

        if gap > 0.5:
            f_lon, f_lat = right_lane[fast_idx]
        elif gap > -7:
            f_lon, f_lat = left_lane[fast_idx]
        else:
            f_lon, f_lat = right_lane[fast_idx]

        t_slow = Thread(target=send_position_ditto, args=(car_slow, s_lat, s_lon))
        t_fast = Thread(target=send_position_ditto, args=(car_fast, f_lat, f_lon))
        t_slow.start()
        t_fast.start()
        t_slow.join()
        t_fast.join()

        time.sleep(0.15)

    time.sleep(2)
    client.loop_stop()

    assert len(ALERTS) > 0, "expected at least one overtaking alert, got none"


if __name__ == "__main__":
    test_overtaking(standalone_get_car_id)
