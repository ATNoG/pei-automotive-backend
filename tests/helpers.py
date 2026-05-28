import json
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1884"))
SIM_DIR = Path(__file__).resolve().parent.parent / "simulations"
ROADS_DIR = SIM_DIR / "roads"

sys.path.insert(0, str(SIM_DIR))
from send_position_ditto import put_features  # noqa: E402

_ditto_session: requests.Session | None = None


def _get_ditto_session() -> requests.Session:
    global _ditto_session
    if _ditto_session is None:
        _ditto_session = requests.Session()
        _ditto_session.auth = (os.getenv("DITTO_USER", ""), os.getenv("DITTO_PASS", ""))
    return _ditto_session


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


# Direct Ditto helpers (bypass Hono for speed-sensitive tests)
_DEVICE_META_CACHE: dict[str, dict] = {}
_ditto_session: "_requests.Session | None" = None


def _load_device_meta(car_name: str) -> dict:
    if car_name not in _DEVICE_META_CACHE:
        meta_file = SIM_DIR / "devices" / f"{car_name}.json"
        _DEVICE_META_CACHE[car_name] = _json.loads(meta_file.read_text())
    return _DEVICE_META_CACHE[car_name]


def _get_ditto_session() -> "_requests.Session":
    global _ditto_session
    if _ditto_session is None:
        _ditto_session = _requests.Session()
        _ditto_session.auth = (
            os.getenv("DITTO_USER", ""),
            os.getenv("DITTO_PASS", ""),
        )
    return _ditto_session


def send_position_ditto(car_name: str, lat: float, lon: float, altitude: float = 0.0) -> None:
    """Send a position update directly to Ditto REST API, bypassing Hono.

    Uses an in-process session (no subprocess) so wall-clock time between calls
    stays ~50 ms — required for position_processor to compute realistic speeds.
    """
    meta_file = SIM_DIR / "devices" / f"{car_name}.json"
    meta = json.loads(meta_file.read_text())
    api_url = os.getenv("DITTO_API_URL", "").rstrip("/")
    body = {
        "referenceTime": datetime.now(timezone.utc).isoformat(),
        "referencePosition": {
            "latitude": lat,
            "longitude": lon,
            "positionConfidenceEllipse": {
                "semiMajorConfidence": 4095,
                "semiMinorConfidence": 4095,
                "semiMajorOrientation": 900,
            },
            "altitude": {
                "altitudeValue": altitude,
                "altitudeConfidence": "unavailable",
            },
        },
        "modemStatus": {
            "mcc": 0,
            "mnc": 0,
            "ratMode": "NR",
            "nr": {"rsrq": 0, "rsrp": 0, "snr": 0, "pci": 0},
            "lte": {"rsrq": 0, "rsrp": 0, "rssi": 0, "snr": 0, "pci": 0},
        },
    }
    r = _get_ditto_session().put(
        f"{api_url}/api/2/things/{meta['thing_id']}/features/ModemStatus/properties",
        json=body,
        timeout=10,
    )
    if r.status_code not in (200, 204):
        raise RuntimeError(
            f"Ditto PUT failed for {car_name}: {r.status_code} {r.text[:120]}"
        )
