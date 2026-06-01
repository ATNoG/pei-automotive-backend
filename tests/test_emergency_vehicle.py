import json
import time
from threading import Thread

import pytest

from helpers import (
    MQTT_HOST, MQTT_PORT, ROADS_DIR,
    ensure_car_exists, send_position_ditto, standalone_get_car_id, make_mqtt_client,
)

ALERTS = []


def on_message(client, userdata, msg):
    ALERTS.append(json.loads(msg.payload.decode()))


@pytest.mark.skip(reason="For now, no emergency")
def test_emergency_vehicle(get_car_id):
    car_regular = get_car_id("ev-test-regular")
    car_emergency = get_car_id("ev-test-emergency")

    ALERTS.clear()

    ensure_car_exists(car_regular, emergency=False)
    ensure_car_exists(car_emergency, emergency=True)

    client = make_mqtt_client()
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe("alerts/emergency_vehicle/+")
    client.loop_start()

    with open(ROADS_DIR / "right_lane.json") as f:
        right_lane = json.load(f)["features"][0]["geometry"]["coordinates"]
    with open(ROADS_DIR / "left_lane.json") as f:
        left_lane = json.load(f)["features"][0]["geometry"]["coordinates"]

    for i in range(0, len(right_lane) - 51, 1):
        regular_idx = i + 4
        ev_idx = round(i * 1.6)

        if regular_idx >= len(right_lane) or ev_idx >= len(right_lane):
            break

        r_lon, r_lat = right_lane[regular_idx]
        gap = regular_idx - ev_idx

        if gap > 0.5:
            e_lon, e_lat = right_lane[ev_idx]
        elif gap > -7:
            e_lon, e_lat = left_lane[ev_idx]
        else:
            e_lon, e_lat = right_lane[ev_idx]

        t_regular = Thread(target=send_position_ditto, args=(car_regular, r_lat, r_lon))
        t_ev = Thread(target=send_position_ditto, args=(car_emergency, e_lat, e_lon))
        t_regular.start()
        t_ev.start()
        t_regular.join()
        t_ev.join()

        time.sleep(0.01)

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
