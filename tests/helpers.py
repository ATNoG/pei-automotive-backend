import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1884"))
MQTT_USER = os.getenv("MQTT_BROKER_USER", "")
MQTT_PASS = os.getenv("MQTT_BROKER_PASSWORD", "")
SIM_DIR = Path(__file__).resolve().parent.parent / "simulations"
ROADS_DIR = SIM_DIR / "roads"


def make_mqtt_client() -> mqtt.Client:
    """Create an authenticated MQTT client. Credentials are read from .env."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if MQTT_USER and MQTT_PASS:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    return client


def ensure_car_exists(car_name: str, emergency: bool = False) -> None:
    """create car in simulation if it doesn't exist."""
    meta = SIM_DIR / "devices" / f"{car_name}.json"
    if not meta.exists():
        cmd = [sys.executable, str(SIM_DIR / "create_car.py"), car_name]
        if emergency:
            cmd.append("--emergency")
        subprocess.run(cmd, check=True)


def send_position(car_name: str, lat: float, lon: float) -> None:
    """send a single gps position update for a car."""
    result = subprocess.run(
        [sys.executable, str(SIM_DIR / "send_position.py"), car_name, str(lat), str(lon)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"failed to send position for {car_name}: {result.stderr}")


def send_positions_parallel(car_positions: list[tuple[str, float, float]]) -> None:
    """send gps positions for multiple cars simultaneously."""
    with ThreadPoolExecutor(max_workers=len(car_positions)) as executor:
        futures = [executor.submit(send_position, *pos) for pos in car_positions]
        for future in as_completed(futures):
            future.result()


def standalone_get_car_id(base_name: str) -> str:
    """generate a unique car id for standalone test execution."""
    return f"{base_name}-{uuid.uuid4().hex[:8]}"
