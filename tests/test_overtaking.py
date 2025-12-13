import json
import time
import subprocess
from pathlib import Path
from threading import Thread
import paho.mqtt.client as mqtt

SIM_DIR = Path(__file__).resolve().parent.parent / "simulations"
ROADS_DIR = Path(__file__).resolve().parent.parent / "simulations/roads"
ALERTS = []

def ensure_car_exists(car_name: str) -> None:
    meta = SIM_DIR / "devices" / f"{car_name}.json"
    if not meta.exists():
        subprocess.run(["python3", str(SIM_DIR / "create_car.py"), car_name], check=True)

def on_overtaking_alert(client, userdata, msg):
    payload = json.loads(msg.payload.decode())
    ALERTS.append(payload)

def send_position(car_name: str, lat: float, lon: float) -> None:
    subprocess.run([
        "python3", str(SIM_DIR / "send_position.py"),
        car_name, str(lat), str(lon)
    ], check=True)

def test_overtaking():
    car_slow = "overtaking-car-front" # victim
    car_fast = "overtaking-car-behind" # overtaker

    ensure_car_exists(car_slow)
    ensure_car_exists(car_fast)

    client = mqtt.Client()
    client.connect("localhost", 1884)
    client.subscribe("alerts/overtaking")
    client.on_message = on_overtaking_alert
    client.loop_start()

    with open(ROADS_DIR / "right_lane.json") as f:
        right_lane = json.load(f)["features"][0]["geometry"]["coordinates"]
    with open(ROADS_DIR / "left_lane.json") as f:
        left_lane = json.load(f)["features"][0]["geometry"]["coordinates"]


    for i in range(0, len(right_lane)-51, 3):
        # slow car starts ahead (index + 4) but now moves 3 steps per iteration
        slow_idx = i + 4

        # fast car starts behind (index 0)
        fast_idx = round(i*1.6)

        if slow_idx >= len(right_lane) or fast_idx >= len(right_lane):
            break

        s_lon, s_lat = right_lane[slow_idx]

        gap = slow_idx - fast_idx # positive = Slow car is ahead

        if gap > 0.5:
            # fast car is far behind -> Right Lane
            f_lon, f_lat = right_lane[fast_idx]
        elif gap > -7:
            # fast car is passing -> Stay in Left Lane until clearly ahead
            f_lon, f_lat = left_lane[fast_idx]
        else:
            # fast car is well ahead -> return to Right Lane
            f_lon, f_lat = right_lane[fast_idx]

        # send positions in parallel using threads
        thread_slow = Thread(target=send_position, args=(car_slow, s_lat, s_lon))
        thread_fast = Thread(target=send_position, args=(car_fast, f_lat, f_lon))
        
        thread_slow.start()
        thread_fast.start()
        
        thread_slow.join()
        thread_fast.join()
        
        time.sleep(0.01)

    time.sleep(1)
    client.loop_stop()

    assert len(ALERTS) > 0, "Expected at least one overtaking alert, got none"

if __name__ == "__main__":
    test_overtaking()
