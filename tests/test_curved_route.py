import json
import time

from helpers import (
    MQTT_HOST, MQTT_PORT, ROADS_DIR,
    ensure_car_exists, send_position_ditto, standalone_get_car_id, make_mqtt_client,
)

POSITION_UPDATES = []


def on_message(client, userdata, msg):
    try:
        POSITION_UPDATES.append(json.loads(msg.payload.decode()))
    except Exception as e:
        print(f"error processing message: {e}")


def _interpolate(coords, steps_per_segment=5):
    interpolated = []
    for i in range(len(coords) - 1):
        lon1, lat1 = coords[i]
        lon2, lat2 = coords[i + 1]
        for j in range(steps_per_segment):
            t = j / steps_per_segment
            interpolated.append((
                lon1 + (lon2 - lon1) * t,
                lat1 + (lat2 - lat1) * t,
            ))
    interpolated.append(coords[-1])
    return interpolated


def test_curved_route(get_car_id):
    car = get_car_id("curved-route-car")
    ensure_car_exists(car)

    POSITION_UPDATES.clear()

    client = make_mqtt_client()
    client.on_message = on_message
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe("cars/updates/+")
    client.loop_start()

    with open(ROADS_DIR / "route.json") as f:
        coords = json.load(f)["features"][0]["geometry"]["coordinates"]

    fine_coords = _interpolate(coords, steps_per_segment=5)

    for lon, lat in fine_coords:
        send_position_ditto(car, lat, lon)
        time.sleep(0.02)

    time.sleep(0.2)
    client.loop_stop()

    assert len(POSITION_UPDATES) > 0, "expected position updates, got none"


if __name__ == "__main__":
    test_curved_route(standalone_get_car_id)
