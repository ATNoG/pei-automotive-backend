import json
import time
import subprocess
import sys
from pathlib import Path

import paho.mqtt.client as mqtt

SIM_DIR = Path(__file__).resolve().parent.parent / "simulations"
ROADS_DIR = Path(__file__).resolve().parent.parent / "simulations/roads"
ALERTS = []


def ensure_car_exists(car_name: str) -> None:
    meta = SIM_DIR / "devices" / f"{car_name}.json"
    if not meta.exists():
        subprocess.run(
            [sys.executable, str(SIM_DIR / "create_car.py"), car_name],
            check=True,
        )


def on_speed_alert(client, userdata, msg):
    ALERTS.append(json.loads(msg.payload.decode()))


def test_speeding():
    car = "speed-car"
    ensure_car_exists(car)

    # subscribe to speed alerts
    client = mqtt.Client()
    client.connect("localhost", 1884)
    client.subscribe("alerts/speed")
    client.on_message = on_speed_alert
    client.loop_start()

    # load right lane coordinates
    with open(ROADS_DIR / "right_lane.json") as f:
        coords = json.load(f)["features"][0]["geometry"]["coordinates"]

    # This reliably exceeds the 80 km/h limit for alert triggering
    for i in range(0, len(coords), 3):
        lon, lat = coords[i]
        subprocess.run(
            [
                sys.executable,
                str(SIM_DIR / "send_position.py"),
                car,
                str(lat),
                str(lon),
            ],
            check=True,
        )
        time.sleep(0.01)

    time.sleep(1)
    client.loop_stop()

    assert len(ALERTS) > 0, "Expected at least one speed alert, got none"
