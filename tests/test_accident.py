import json
import os
import time
import subprocess
import sys
import queue
import threading
from pathlib import Path
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed

import paho.mqtt.client as mqtt

SIM_DIR = Path(__file__).resolve().parent.parent / "simulations"
ROADS_DIR = SIM_DIR / "roads"

# Configuration
MQTT_HOST = os.getenv("TEST_MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("TEST_MQTT_PORT", "1884"))
POSITION_INTERVAL = 0.01 
STEP_SIZE = 4  # Points to skip for higher speed
PHASE1_PERCENTAGE = 0.6
ALERT_TIMEOUT = 3.0
THREAD_TIMEOUT = 60.0 


def ensure_car_exists(car_name: str) -> None:
    """Ensure car is registered. Ignores if already exists."""
    meta = SIM_DIR / "devices" / f"{car_name}.json"
    if not meta.exists():
        subprocess.run(
            [sys.executable, str(SIM_DIR / "create_car.py"), car_name],
            check=True,
        )


def send_position(car_name: str, lat: float, lon: float) -> None:
    """Send a single GPS position update for a car."""
    result = subprocess.run(
        [sys.executable, str(SIM_DIR / "send_position.py"), car_name, str(lat), str(lon)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to send position for {car_name}: {result.stderr}")


def send_positions_parallel(car_positions: list[tuple[str, float, float]]) -> None:
    """Send GPS positions for multiple cars simultaneously."""
    with ThreadPoolExecutor(max_workers=len(car_positions)) as executor:
        futures = [executor.submit(send_position, *pos) for pos in car_positions]
        for future in as_completed(futures):
            future.result()


@contextmanager
def mqtt_alert_collector(topics: list[str]):
    alert_queue = queue.Queue()
    
    def on_message(client, userdata, msg):
        car_id = msg.topic.split("/")[-1]
        payload = json.loads(msg.payload.decode())
        if payload.get("notification_type") == "accident_alert":
            alert_queue.put((car_id, payload))
    
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    try:
        client.on_message = on_message
        client.connect(MQTT_HOST, MQTT_PORT)
        for topic in topics:
            client.subscribe(topic, qos=1)
        client.loop_start()
        yield client, alert_queue
    finally:
        client.loop_stop()
        client.disconnect()


def collect_alerts(alert_queue: queue.Queue, timeout: float = ALERT_TIMEOUT) -> dict:
    """Collect accident alerts from queue with timeout."""
    alerts = {"car-behind": [], "car-ahead": []}
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            car_id, alert = alert_queue.get(timeout=0.1)
            if car_id in alerts:
                alerts[car_id].append(alert)
        except queue.Empty:
            pass
    return alerts


def test_accident_directional_notification():
    accident_car = "accident-car"
    car_behind = "car-behind"
    car_ahead = "car-ahead"

    ensure_car_exists(accident_car)
    ensure_car_exists(car_behind)
    ensure_car_exists(car_ahead)

    # Use context manager for proper cleanup on success or failure
    with mqtt_alert_collector([
        f"alerts/accident/{car_behind}",
        f"alerts/accident/{car_ahead}",
    ]) as (client, alert_queue):
        
        # Load routes
        with open(ROADS_DIR / "ponte_barra_accident.json") as f:
            accident_coords = json.load(f)["features"][0]["geometry"]["coordinates"]
        with open(ROADS_DIR / "ponte_barra_behind.json") as f:
            behind_coords = json.load(f)["features"][0]["geometry"]["coordinates"]
        with open(ROADS_DIR / "ponte_barra_ahead.json") as f:
            ahead_coords = json.load(f)["features"][0]["geometry"]["coordinates"]

        min_len = min(len(accident_coords), len(behind_coords), len(ahead_coords))
        phase1_end = int(min_len * PHASE1_PERCENTAGE)
        phase2_end = min_len

        # PHASE 1: All cars driving normally at high speed
        for i in range(0, phase1_end, STEP_SIZE):
            acc_lon, acc_lat = accident_coords[i]
            beh_lon, beh_lat = behind_coords[i]
            ahe_lon, ahe_lat = ahead_coords[i]

            send_positions_parallel([
                (accident_car, acc_lat, acc_lon),
                (car_behind, beh_lat, beh_lon),
                (car_ahead, ahe_lat, ahe_lon),
            ])
            time.sleep(POSITION_INTERVAL)

        # PHASE 2: accident-car stops while others continue
        accident_idx = phase1_end
        accident_lon, accident_lat = accident_coords[accident_idx]

        def accident_thread():
            """Accident car: send repeated updates at same position (stopped)."""
            for _ in range(8):
                send_position(accident_car, accident_lat, accident_lon)
                time.sleep(0.3)

        def other_cars_thread():
            """Other cars continue moving toward/past accident location."""
            for i in range(accident_idx, phase2_end, STEP_SIZE):
                beh_lon, beh_lat = behind_coords[i]
                ahe_lon, ahe_lat = ahead_coords[i]
                send_positions_parallel([
                    (car_behind, beh_lat, beh_lon),
                    (car_ahead, ahe_lat, ahe_lon),
                ])
                time.sleep(POSITION_INTERVAL)

        # Run both in parallel with timeout
        t_accident = threading.Thread(target=accident_thread)
        t_others = threading.Thread(target=other_cars_thread)

        t_accident.start()
        t_others.start()

        t_accident.join(timeout=THREAD_TIMEOUT)
        t_others.join(timeout=THREAD_TIMEOUT)

        if t_accident.is_alive() or t_others.is_alive():
            raise TimeoutError("Simulation threads did not complete in time")

        # Wait for pipeline processing and collect alerts
        time.sleep(2)
        alerts = collect_alerts(alert_queue, timeout=ALERT_TIMEOUT)

    # Assertions
    assert len(alerts["car-behind"]) > 0, (
        f"Car BEHIND should receive accident alerts. "
        f"Got {len(alerts['car-behind'])} alerts. "
        f"Check that accident-car speed reached >30 km/h before stopping."
    )

    assert len(alerts["car-ahead"]) == 0, (
        f"Car AHEAD should NOT receive alerts (accident is behind it). "
        f"Got {len(alerts['car-ahead'])} alerts."
    )


if __name__ == "__main__":
    test_accident_directional_notification()
    print("\nTest passed: Only car-behind (approaching accident) was notified")
