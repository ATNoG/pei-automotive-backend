import json
import time
import subprocess
import sys
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt

SIM_DIR = Path(__file__).resolve().parent.parent / "simulations"
ROADS_DIR = Path(__file__).resolve().parent.parent / "simulations/roads"
ALERTS = []
CAR_UPDATES = []


def ensure_car_exists(car_name: str) -> None:
    meta = SIM_DIR / "devices" / f"{car_name}.json"
    if not meta.exists():
        subprocess.run(
            [sys.executable, str(SIM_DIR / "create_car.py"), car_name],
            check=True,
        )


def on_speed_alert(client, userdata, msg):
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

    # subscribe to speed alerts and car updates
    client = mqtt.Client()
    client.connect("localhost", 1884)
    client.subscribe("alerts/speed")
    client.subscribe("cars/updates")
    client.on_message = on_speed_alert
    client.loop_start()

    # load right lane coordinates
    with open(ROADS_DIR / "right_lane_speeding.json") as f:
        coords = json.load(f)["features"][0]["geometry"]["coordinates"]

    for i in range(0, len(coords)-40, 5):
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

    time.sleep(2)
    client.loop_stop()


    assert len(ALERTS) > 0, "Expected at least one speed alert, got none"

    # Verify that car updates contain the speed_limit_kmh field
    updates_with_limit = [
        u for u in CAR_UPDATES
        if u.get("car_id", "").startswith("speed-car")
        and u.get("speed_limit_kmh") is not None
    ]
    assert len(updates_with_limit) > 0, (
        f"Expected car updates with speed_limit_kmh field, "
        f"but none had it. Sample update: {CAR_UPDATES[:1]}"
    )

    # Verify speed_limit_kmh is a sensible numeric value
    for update in updates_with_limit:
        limit = update["speed_limit_kmh"]
        assert isinstance(limit, (int, float)), (
            f"speed_limit_kmh should be numeric, got {type(limit)}: {limit}"
        )
        assert 0 < limit <= 300, (
            f"speed_limit_kmh should be between 0 and 300, got {limit}"
        )

    # Verify the speed alert also contains the speed_limit_kmh
    for alert in ALERTS:
        assert "speed_limit_kmh" in alert, (
            f"Speed alert missing speed_limit_kmh field: {alert}"
        )
        
if __name__ == "__main__":
    def get_car_id(base_name: str) -> str:
        return f"{base_name}-{str(uuid.uuid4())[:8]}"
    test_speeding(get_car_id)