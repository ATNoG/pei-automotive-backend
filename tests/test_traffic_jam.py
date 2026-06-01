import json
import time
from contextlib import contextmanager
from threading import Thread

import paho.mqtt.client as mqtt

from helpers import (
    MQTT_HOST, MQTT_PORT, ROADS_DIR,
    ensure_car_exists, send_position_ditto,
)


@contextmanager
def mqtt_alert_collector(topics: list[str]):
    import queue
    alert_queue = queue.Queue()
    all_alerts = []

    def on_message(client, userdata, msg):
        payload = json.loads(msg.payload.decode())
        alert_queue.put((msg.topic, payload))
        all_alerts.append((msg.topic, payload))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    try:
        client.on_message = on_message
        client.connect(MQTT_HOST, MQTT_PORT)
        for topic in topics:
            client.subscribe(topic, qos=1)
        client.loop_start()
        time.sleep(0.3)
        yield client, alert_queue, all_alerts
    finally:
        client.loop_stop()
        client.disconnect()


def _send_parallel(positions: list[tuple[str, float, float]]):
    threads = [Thread(target=send_position_ditto, args=p) for p in positions]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_traffic_jam(get_car_id):
    lead_car = get_car_id("minimal-jam-lead")
    jam_cars = [get_car_id(f"minimal-jam-car-{i}") for i in range(1, 6)]

    all_cars = [lead_car] + jam_cars

    for car in all_cars:
        ensure_car_exists(car)

    alert_topics = [
        "alerts/traffic_jam/+",
    ]

    with mqtt_alert_collector(alert_topics) as (client, alert_queue, all_alerts):

        with open(ROADS_DIR / "highway.json") as f:
            highway_coords = json.load(f)["features"][0]["geometry"]["coordinates"]

        phase1_iterations = 6
        for iteration in range(phase1_iterations):
            positions = []

            lead_idx = iteration + 15
            if lead_idx < len(highway_coords):
                lat, lon = highway_coords[lead_idx]
                positions.append((lead_car, lat, lon))

            for i, car in enumerate(jam_cars):
                car_idx = lead_idx - (i + 1)
                if 0 <= car_idx < len(highway_coords):
                    lat, lon = highway_coords[car_idx]
                    positions.append((car, lat, lon))

            _send_parallel(positions)
            time.sleep(0.05)

        phase2_iterations = 15
        base_idx = (phase1_iterations - 1) + 15
        for iteration in range(phase2_iterations):
            positions = []
            cluster_idx = base_idx + iteration + 1

            if cluster_idx < len(highway_coords):
                lat, lon = highway_coords[cluster_idx]
                positions.append((lead_car, lat, lon))

            for i, car in enumerate(jam_cars):
                car_idx = cluster_idx - (i + 1)
                if 0 <= car_idx < len(highway_coords):
                    lat, lon = highway_coords[car_idx]
                    positions.append((car, lat, lon))

            _send_parallel(positions)
            time.sleep(1.5)

    jam_alerts_dedup = {}
    for t, a in all_alerts:
        if a.get("alert_type") == "traffic_jam":
            jam_alerts_dedup[a.get("jam_id")] = a
    jam_alerts = list(jam_alerts_dedup.values())
    assert len(jam_alerts) > 0, (
        "Expected traffic jam detection with 5 slow/stopped cars. "
        f"Got {len(jam_alerts)} traffic jam alerts."
    )

    if jam_alerts:
        max_severity = max(a.get("severity", 0) for a in jam_alerts)
        assert max_severity >= 5, (
            f"Expected traffic jam with 5+ cars, got severity={max_severity}"
        )


if __name__ == "__main__":
    def get_car_id(base_name: str) -> str:
        import uuid
        return f"{base_name}-{str(uuid.uuid4())[:8]}"
    test_traffic_jam(get_car_id)
