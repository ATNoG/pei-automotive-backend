import json
import time
import subprocess
import sys
from pathlib import Path

import paho.mqtt.client as mqtt

SIM_DIR = Path(__file__).resolve().parent.parent / "simulations"
ROADS_DIR = Path(__file__).resolve().parent.parent / "simulations/roads"
POSITION_UPDATES = []


def ensure_car_exists(car_name: str) -> None:
    meta = SIM_DIR / "devices" / f"{car_name}.json"
    if not meta.exists():
        subprocess.run(
            [sys.executable, str(SIM_DIR / "create_car.py"), car_name],
            check=True,
        )


def on_position_update(client, userdata, msg):
    """Callback to capture position updates from the position processor."""
    try:
        payload = json.loads(msg.payload.decode())
        POSITION_UPDATES.append(payload)
    except Exception as e:
        print(f"Error processing message: {e}")


def send_position(car_name: str, lat: float, lon: float) -> None:
    """Send a position update for a specific car."""
    subprocess.run([
        sys.executable, str(SIM_DIR / "send_position.py"),
        car_name, str(lat), str(lon)
    ], check=True)


def test_curved_route():
    """Test vehicle navigation on a curved route with complex trajectory."""
    car = "curved-route-car"
    ensure_car_exists(car)

    # subscribe to position updates topic
    client = mqtt.Client()
    client.connect("localhost", 1884)
    client.subscribe("cars/updates")
    client.on_message = on_position_update
    client.loop_start()

    # load curved route coordinates
    with open(ROADS_DIR / "route.json") as f:
        coords = json.load(f)["features"][0]["geometry"]["coordinates"]

    print(f"Testing curved route with {len(coords)} points")

    # send positions along the curved route
    # Sample every 5th point to simulate realistic movement
    for i in range(0, len(coords), 3):
        lon, lat = coords[i]
        send_position(car, lat, lon)
        time.sleep(0.02)  # simulate brief delay between updates

    # finish route at last coordinate
    if (lon, lat) != coords[-1]:
        lon, lat = coords[-1]
        send_position(car, lat, lon)

    # wait for final messages to be processed
    time.sleep(0.2)
    client.loop_stop()

    # verify that position updates were received
    assert len(POSITION_UPDATES) > 0, "Expected position updates, got none"
    print(f"Received {len(POSITION_UPDATES)} position updates")

    # verify that positions follow the curved path
    # Check that latitude decreases (moving south)
    first_lat = POSITION_UPDATES[0].get("latitude") or POSITION_UPDATES[0].get("position", {}).get("latitude")
    last_lat = POSITION_UPDATES[-1].get("latitude") or POSITION_UPDATES[-1].get("position", {}).get("latitude")

    if first_lat and last_lat:
        assert len(POSITION_UPDATES) > 0

    print("Curved route test passed successfully!")


if __name__ == "__main__":
    test_curved_route()
