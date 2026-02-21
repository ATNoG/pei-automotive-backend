import json
import time
import subprocess
import sys
import uuid
from pathlib import Path
from threading import Thread
import paho.mqtt.client as mqtt

SIM_DIR = Path(__file__).resolve().parent.parent / "simulations"
ROADS_DIR = Path(__file__).resolve().parent.parent / "simulations/roads"
ALERTS = []


def ensure_car_exists(car_name: str, emergency: bool = False) -> None:
    meta = SIM_DIR / "devices" / f"{car_name}.json"
    if not meta.exists():
        cmd = [sys.executable, str(SIM_DIR / "create_car.py"), car_name]
        if emergency:
            cmd.append("--emergency")
        subprocess.run(cmd, check=True)


def on_ev_alert(client, userdata, msg):
    payload = json.loads(msg.payload.decode())
    ALERTS.append(payload)


def send_position(car_name: str, lat: float, lon: float) -> None:
    subprocess.run(
        [sys.executable, str(SIM_DIR / "send_position.py"),
         car_name, str(lat), str(lon)],
        check=True,
    )


def test_emergency_vehicle(get_car_id):
    car_regular = get_car_id("ev-test-regular")      # regular car (victim)
    car_emergency = get_car_id("ev-test-emergency")  # emergency vehicle

    ensure_car_exists(car_regular, emergency=False)
    ensure_car_exists(car_emergency, emergency=True)

    client = mqtt.Client()
    client.connect("localhost", 1884)
    client.subscribe("alerts/emergency_vehicle")
    client.on_message = on_ev_alert
    client.loop_start()

    with open(ROADS_DIR / "right_lane.json") as f:
        right_lane = json.load(f)["features"][0]["geometry"]["coordinates"]
    with open(ROADS_DIR / "left_lane.json") as f:
        left_lane = json.load(f)["features"][0]["geometry"]["coordinates"]

    # Same movement pattern as overtaking test:
    # regular car on right lane, emergency vehicle approaches from behind on left lane
    for i in range(0, len(right_lane) - 51, 3):
        # regular car starts ahead (index + 4)
        regular_idx = i + 4

        # emergency vehicle starts behind (index 0) but moves faster
        ev_idx = round(i * 1.6)

        if regular_idx >= len(right_lane) or ev_idx >= len(right_lane):
            break

        r_lon, r_lat = right_lane[regular_idx]

        gap = regular_idx - ev_idx

        if gap > 0.5:
            # EV is far behind -> right lane
            e_lon, e_lat = right_lane[ev_idx]
        elif gap > -7:
            # EV is passing -> left lane
            e_lon, e_lat = left_lane[ev_idx]
        else:
            # EV is well ahead -> return to right lane
            e_lon, e_lat = right_lane[ev_idx]

        # send positions in parallel
        thread_regular = Thread(target=send_position, args=(car_regular, r_lat, r_lon))
        thread_ev = Thread(target=send_position, args=(car_emergency, e_lat, e_lon))

        thread_regular.start()
        thread_ev.start()

        thread_regular.join()
        thread_ev.join()

        time.sleep(0.01)

    time.sleep(1)
    client.loop_stop()


    assert len(ALERTS) > 0, "Expected at least one emergency vehicle alert, got none"
    # Verify alert structure
    alert = ALERTS[0]
    assert alert["alert_type"] == "emergency_vehicle_nearby"
    assert alert["emergency_vehicle_id"] is not None
    assert alert["regular_car_id"] is not None
    assert alert["distance_m"] <= 500


if __name__ == "__main__":
    def get_car_id(base_name: str) -> str:
        return f"{base_name}-{str(uuid.uuid4())[:8]}"
    test_emergency_vehicle(get_car_id)
