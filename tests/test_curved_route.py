import json
import time

import paho.mqtt.client as mqtt

from helpers import (
    MQTT_HOST, MQTT_PORT, ROADS_DIR,
    ensure_car_exists, send_position_ditto, standalone_get_car_id,
)

POSITION_UPDATES = []


def on_message(client, userdata, msg):
    try:
        POSITION_UPDATES.append(json.loads(msg.payload.decode()))
    except Exception as e:
        print(f"error processing message: {e}")


def test_curved_route(get_car_id):
    car = get_car_id("curved-route-car")
    ensure_car_exists(car)

    POSITION_UPDATES.clear()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe("cars/updates/+")
    client.loop_start()

    with open(ROADS_DIR / "route.json") as f:
        coords = json.load(f)["features"][0]["geometry"]["coordinates"]

    for i in range(0, len(coords), 3):
        lon, lat = coords[i]
        send_position_ditto(car, lat, lon)
        time.sleep(0.02)

    last_lon, last_lat = coords[-1]
    send_position_ditto(car, last_lat, last_lon)

    time.sleep(0.2)
    client.loop_stop()

    assert len(POSITION_UPDATES) > 0, "expected position updates, got none"


if __name__ == "__main__":
    test_curved_route(standalone_get_car_id)
