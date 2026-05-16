import json
import queue
import threading
import time
from contextlib import contextmanager

import paho.mqtt.client as mqtt
from helpers import (
    MQTT_HOST,
    MQTT_PORT,
    ROADS_DIR,
    ensure_car_exists,
    send_position,
    send_positions_parallel,
    standalone_get_car_id, make_mqtt_client,
)

POSITION_INTERVAL = 0.05
STEP_SIZE = 5
PHASE1_PERCENTAGE = 0.80
ALERT_TIMEOUT = 3.0
THREAD_TIMEOUT = 60.0


@contextmanager
def mqtt_alert_collector(topics: list[str]):
    alert_queue = queue.Queue()

    def on_message(client, userdata, msg):
        car_id = msg.topic.split("/")[-1]
        payload = json.loads(msg.payload.decode())
        if payload.get("notification_type") == "accident_alert":
            alert_queue.put((car_id, payload))

    client = make_mqtt_client()
    try:
        client.on_message = on_message
        client.connect(MQTT_HOST, MQTT_PORT)
        for topic in topics:
            client.subscribe(topic, qos=1)
        client.loop_start()
        time.sleep(0.2)
        yield client, alert_queue
    finally:
        client.loop_stop()
        client.disconnect()


def collect_alerts(
    alert_queue: queue.Queue, car_ids: list[str], timeout: float = ALERT_TIMEOUT
) -> dict:
    """collect accident alerts from queue with timeout."""
    alerts = {car_id: [] for car_id in car_ids}
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            car_id, alert = alert_queue.get(timeout=0.1)
            if car_id in alerts:
                alerts[car_id].append(alert)
        except queue.Empty:
            pass
    return alerts


def test_accident_directional_notification(get_car_id):
    accident_car = get_car_id("accident-car")
    car_behind = get_car_id("car-behind")
    car_ahead = get_car_id("car-ahead")

    ensure_car_exists(accident_car)
    ensure_car_exists(car_behind)
    ensure_car_exists(car_ahead)

    # use context manager for proper cleanup on success or failure
    with mqtt_alert_collector(
        [
            f"alerts/accident/{car_behind}",
            f"alerts/accident/{car_ahead}",
        ]
    ) as (client, alert_queue):
        # load routes
        with open(ROADS_DIR / "ponte_barra_accident.json") as f:
            accident_coords = json.load(f)["features"][0]["geometry"]["coordinates"]
        with open(ROADS_DIR / "ponte_barra_behind.json") as f:
            behind_coords = json.load(f)["features"][0]["geometry"]["coordinates"]
        with open(ROADS_DIR / "ponte_barra_ahead.json") as f:
            ahead_coords = json.load(f)["features"][0]["geometry"]["coordinates"]

        min_len = min(len(accident_coords), len(behind_coords), len(ahead_coords))
        phase1_end = int(min_len * PHASE1_PERCENTAGE)

        # phase 1: all cars driving normally at high speed
        last_idx = 0
        for i in range(0, phase1_end, STEP_SIZE):
            acc_lon, acc_lat = accident_coords[i]
            beh_lon, beh_lat = behind_coords[i]
            ahe_lon, ahe_lat = ahead_coords[i]

            send_positions_parallel(
                [
                    (accident_car, acc_lat, acc_lon),
                    (car_behind, beh_lat, beh_lon),
                    (car_ahead, ahe_lat, ahe_lon),
                ]
            )
            time.sleep(POSITION_INTERVAL)
            last_idx = i

        # phase 2: accident-car stops while others continue
        accident_idx = last_idx
        accident_lon, accident_lat = accident_coords[accident_idx]

        # calculate how many iterations we need
        num_iterations = 8

        def accident_thread():
            """accident car: send repeated updates at same position (stopped)."""
            for _ in range(num_iterations):
                send_position(accident_car, accident_lat, accident_lon)
                time.sleep(POSITION_INTERVAL)

        def other_cars_thread():
            """other cars continue moving past accident location."""
            for iteration in range(num_iterations):
                idx = accident_idx + STEP_SIZE + (iteration * STEP_SIZE)

                # keep moving if within bounds, else maintain last position
                if idx < len(behind_coords) and idx < len(ahead_coords):
                    beh_lon, beh_lat = behind_coords[idx]
                    ahe_lon, ahe_lat = ahead_coords[idx]
                else:
                    beh_lon, beh_lat = behind_coords[-1]
                    ahe_lon, ahe_lat = ahead_coords[-1]

                send_positions_parallel(
                    [
                        (car_behind, beh_lat, beh_lon),
                        (car_ahead, ahe_lat, ahe_lon),
                    ]
                )
                time.sleep(POSITION_INTERVAL)

        # run both in parallel with timeout
        t_accident = threading.Thread(target=accident_thread)
        t_others = threading.Thread(target=other_cars_thread)

        t_accident.start()
        t_others.start()

        t_accident.join(timeout=THREAD_TIMEOUT)
        t_others.join(timeout=THREAD_TIMEOUT)

        if t_accident.is_alive() or t_others.is_alive():
            raise TimeoutError("simulation threads did not complete in time")

        # wait for pipeline processing and collect alerts
        time.sleep(3)
        alerts = collect_alerts(
            alert_queue, [car_behind, car_ahead], timeout=ALERT_TIMEOUT
        )

    assert len(alerts[car_behind]) > 0, (
        f"car BEHIND should receive accident alerts. "
        f"got {len(alerts[car_behind])} alerts."
    )

    assert len(alerts[car_ahead]) == 0, (
        f"car AHEAD should not receive alerts (accident is behind it). "
        f"got {len(alerts[car_ahead])} alerts."
    )


if __name__ == "__main__":
    test_accident_directional_notification(standalone_get_car_id)
    print("\ntest passed: only car-behind (approaching accident) was notified")
