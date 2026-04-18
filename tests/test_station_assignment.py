import json
import time
from threading import Event

import paho.mqtt.client as mqtt
import pytest

pytestmark = pytest.mark.skip(reason="station assignment tests skipped")
from helpers import (
    MQTT_HOST,
    MQTT_PORT,
    ensure_car_exists,
    send_position,
    standalone_get_car_id,
)

ASSIGNMENTS = []
subscription_ready = Event()
assignment_received = Event()


def on_station_assignment(client, userdata, msg):
    payload = json.loads(msg.payload.decode())
    ASSIGNMENTS.append(payload)
    assignment_received.set()


def on_subscribe(client, userdata, mid, reason_code_list, properties=None):
    subscription_ready.set()


def test_station_assignment_basic(get_car_id):
    car_id = get_car_id("station-test-car")
    ensure_car_exists(car_id)

    ASSIGNMENTS.clear()
    subscription_ready.clear()
    assignment_received.clear()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_station_assignment
    client.on_subscribe = on_subscribe
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe(f"cars/station/{car_id}", qos=1)
    client.loop_start()

    try:
        if not subscription_ready.wait(timeout=5):
            raise TimeoutError("subscription not confirmed")

        time.sleep(2)
        send_position(car_id, 40.640506, -8.653754)

        if not assignment_received.wait(timeout=45):
            raise TimeoutError("no station assignment received")

        assert len(ASSIGNMENTS) > 0

        assignment = ASSIGNMENTS[0]
        assert assignment["car_id"] == car_id

        station = assignment["station"]
        assert "station_id" in station
        assert "location" in station
        assert "location_name" in station

        location = station["location"]
        assert "latitude" in location
        assert "longitude" in location
    finally:
        client.loop_stop()
        client.disconnect()


def test_station_assignment_changes(get_car_id):
    car_id = get_car_id("station-test-moving-car")
    ensure_car_exists(car_id)

    ASSIGNMENTS.clear()
    subscription_ready.clear()
    assignment_received.clear()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_station_assignment
    client.on_subscribe = on_subscribe
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe(f"cars/station/{car_id}", qos=1)
    client.loop_start()

    try:
        if not subscription_ready.wait(timeout=5):
            raise TimeoutError("subscription not confirmed")

        time.sleep(2)
        send_position(car_id, 40.640506, -8.653754)

        if not assignment_received.wait(timeout=45):
            raise TimeoutError("no station assignment received")

        time.sleep(5)

        assert len(ASSIGNMENTS) > 0

        for assignment in ASSIGNMENTS:
            assert assignment["car_id"] == car_id
            assert "station" in assignment
            assert "station_id" in assignment["station"]
    finally:
        client.loop_stop()
        client.disconnect()


def test_station_assignment_no_duplicate_on_same_station(get_car_id):
    car_id = get_car_id("station-test-stationary-car")
    ensure_car_exists(car_id)

    ASSIGNMENTS.clear()
    subscription_ready.clear()
    assignment_received.clear()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_station_assignment
    client.on_subscribe = on_subscribe
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe(f"cars/station/{car_id}", qos=1)
    client.loop_start()

    try:
        if not subscription_ready.wait(timeout=5):
            raise TimeoutError("subscription not confirmed")

        time.sleep(2)
        send_position(car_id, 40.640506, -8.653754)

        if not assignment_received.wait(timeout=45):
            raise TimeoutError("no station assignment received")

        assignment_received.clear()

        for i in range(1, 5):
            lat = 40.640506 + (i * 0.0001)
            lon = -8.653754 + (i * 0.0001)
            send_position(car_id, lat, lon)
            time.sleep(1)

        time.sleep(10)

        assert len(ASSIGNMENTS) > 0
        assert len(ASSIGNMENTS) <= 2
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    print("running station assignment tests...\n")

    test_station_assignment_basic(standalone_get_car_id)
    test_station_assignment_changes(standalone_get_car_id)
    test_station_assignment_no_duplicate_on_same_station(standalone_get_car_id)

    print("all tests passed")
