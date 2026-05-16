import json
import time

import paho.mqtt.client as mqtt

from helpers import (
    MQTT_HOST, MQTT_PORT, ROADS_DIR,
    ensure_car_exists, send_position, standalone_get_car_id, make_mqtt_client,
)

ALERTS = []
CAR_UPDATES = []


def on_message(client, userdata, msg):
    payload = json.loads(msg.payload.decode())
    if msg.topic == "alerts/speed":
        ALERTS.append(payload)
    elif msg.topic == "cars/updates":
        CAR_UPDATES.append(payload)


def test_speeding(get_car_id):
    car = get_car_id("speed-car")
    ensure_car_exists(car)

    ALERTS.clear()
    CAR_UPDATES.clear()

    client = make_mqtt_client()
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe("alerts/speed")
    client.subscribe("cars/updates")
    client.loop_start()

    with open(ROADS_DIR / "right_lane_speeding.json") as f:
        coords = json.load(f)["features"][0]["geometry"]["coordinates"]

    for i in range(0, len(coords) - 40, 5):
        lon, lat = coords[i]
        send_position(car, lat, lon)
        time.sleep(0.01)

    time.sleep(2)
    client.loop_stop()

    assert len(ALERTS) > 0, "expected at least one speed alert, got none"

    updates_with_limit = [
        u for u in CAR_UPDATES
        if u.get("car_id", "").startswith("speed-car") and u.get("speed_limit_kmh") is not None
    ]
    assert len(updates_with_limit) > 0, (
        f"expected car updates with speed_limit_kmh, got none. sample: {CAR_UPDATES[:1]}"
    )

    for update in updates_with_limit:
        limit = update["speed_limit_kmh"]
        assert isinstance(limit, (int, float)), f"speed_limit_kmh should be numeric, got {type(limit)}: {limit}"
        assert 0 < limit <= 300, f"speed_limit_kmh should be between 0 and 300, got {limit}"

    for alert in ALERTS:
        assert "speed_limit_kmh" in alert, f"speed alert missing speed_limit_kmh: {alert}"


if __name__ == "__main__":
    test_speeding(standalone_get_car_id)
