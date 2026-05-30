"""pytest configuration for test suite."""
import json
import os
import socket
import time
import uuid
from typing import List

import paho.mqtt.client as mqtt
import pytest
import requests
import urllib3
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
from urllib3.util.retry import Retry
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from helpers import MQTT_HOST, MQTT_PORT, SIM_DIR

# Load environment variables
load_dotenv()

def pytest_addoption(parser):
    parser.addoption(
        "--fixed-ids",
        action="store_true",
        default=False,
        help="use fixed car ids instead of random uuids (useful for frontend testing)",
    )


def _check_connectivity(host="10.255.38.67", port=80, timeout=2):
    """Check if meteorological station data is available."""
    # First, check basic network connectivity
    try:
        socket.create_connection((host, port), timeout=timeout)
    except (socket.timeout, socket.error):
        return False

    # Then verify the actual weather API has meteo things
    try:
        weather_api_url = os.getenv("WEATHER_API_URL", "")
        weather_user = os.getenv("WEATHER_USER", "")
        weather_pass = os.getenv("WEATHER_PASS", "")

        if not all([weather_api_url, weather_user, weather_pass]):
            return False

        # Try to fetch meteo things from Ditto API
        api_url = weather_api_url.rstrip('/')
        search_url = f"{api_url}/api/2/search/things"
        params = {
            'namespaces': 'meteo',
            'option': 'size(1)'
        }

        response = requests.get(
            search_url,
            params=params,
            auth=HTTPBasicAuth(weather_user, weather_pass),
            timeout=timeout
        )

        if response.status_code == 200:
            data = response.json()
            # Check if we got at least one meteo thing
            if isinstance(data, dict):
                items = data.get('items', [])
                return len(items) > 0
            elif isinstance(data, list):
                return len(data) > 0

        return False
    except Exception:
        return False


_session_cars: set[str] = set()


@pytest.fixture(scope="session", autouse=True)
def _session_ditto_cleanup():
    """After the full test session, delete any cars that were not cleaned up per-test."""
    yield
    if not _session_cars:
        return
    print(f"\n[cleanup] session sweep: checking {len(_session_cars)} cars...")
    for car_id in _session_cars:
        meta_path = SIM_DIR / "devices" / f"{car_id}.json"
        if meta_path.exists():
            _ditto_delete_thing(f"org.acme:{car_id}")
            try:
                meta_path.unlink()
            except Exception:
                pass


@pytest.fixture(scope="session")
def tomastest_available():
    """fixture that indicates if tomastest.com is available."""
    return _check_connectivity()


def pytest_collection_modifyitems(config, items):
    """skip station_assignment tests if stations not available."""
    tomastest_ok = _check_connectivity()

    if not tomastest_ok:
        skip_tomastest = pytest.mark.skip(reason="tomastest.com (10.255.38.67) is not reachable or has no meteo data")
        for item in items:
            if "test_station_assignment" in str(item.fspath):
                item.add_marker(skip_tomastest)


@pytest.fixture(scope="session")
def use_fixed_ids(request):
    return request.config.getoption("--fixed-ids")


@pytest.fixture
def test_car_registry():
    """track all cars created during a test for cleanup."""
    car_ids = []
    yield car_ids
    _cleanup_test_cars(car_ids)


@pytest.fixture
def get_car_id(use_fixed_ids, test_car_registry):
    """return a function that generates car ids and tracks them for cleanup."""
    def _get_car_id(base_name: str) -> str:
        car_id = base_name if use_fixed_ids else f"{base_name}-{uuid.uuid4().hex[:8]}"
        test_car_registry.append(car_id)
        _session_cars.add(car_id)
        return car_id

    return _get_car_id


def _make_cleanup_session(auth: tuple) -> requests.Session:
    session = requests.Session()
    session.auth = auth
    session.verify = False
    retry = Retry(total=5, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


_cleanup_ditto_session: requests.Session | None = None
_cleanup_hono_session: requests.Session | None = None


def _get_cleanup_ditto_session() -> requests.Session:
    global _cleanup_ditto_session
    if _cleanup_ditto_session is None:
        _cleanup_ditto_session = _make_cleanup_session(
            (os.getenv("DITTO_USER", ""), os.getenv("DITTO_PASS", ""))
        )
    return _cleanup_ditto_session


def _get_cleanup_hono_session() -> requests.Session:
    global _cleanup_hono_session
    if _cleanup_hono_session is None:
        _cleanup_hono_session = _make_cleanup_session(
            (os.getenv("HONO_USER", ""), os.getenv("HONO_PASS", ""))
        )
    return _cleanup_hono_session


def _ditto_delete_thing(thing_id: str) -> bool:
    """Delete a Ditto Thing and its Policy, plus the Hono device."""
    ditto_api = os.getenv("DITTO_API_URL", "").rstrip("/")
    hono_api = os.getenv("HONO_API_URL", "").rstrip("/")
    hono_tenant = os.getenv("HONO_TENANT", "")

    for session, url, label, optional in [
        (_get_cleanup_ditto_session(), f"{ditto_api}/api/2/things/{thing_id}", "Ditto Thing", False),
        (_get_cleanup_ditto_session(), f"{ditto_api}/api/2/policies/{thing_id}", "Ditto Policy", False),
        (_get_cleanup_hono_session(), f"{hono_api}/v1/devices/{hono_tenant}/{thing_id}", "Hono Device", True),
    ]:
        try:
            resp = session.delete(url, timeout=15)
            if resp.status_code not in (200, 202, 204, 404):
                print(f"[cleanup] warning: {label} delete returned {resp.status_code}")
            else:
                print(f"[cleanup] {label} deleted: {thing_id}")
        except Exception as e:
            if not optional:
                print(f"[cleanup] warning: {label} delete failed for {thing_id}: {e}")
    return True


def _cleanup_test_cars(car_ids: List[str]) -> None:
    """clean up test cars from Ditto, services, and local device files."""
    if not car_ids:
        return

    print(f"\n[cleanup] removing {len(car_ids)} test cars...")

    # Delete Ditto Things first so no stale events reach the WS/proximity_filter.
    for cid in car_ids:
        meta_path = SIM_DIR / "devices" / f"{cid}.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            thing_id = meta.get("thing_id", f"org.acme:{cid}")
        else:
            thing_id = f"org.acme:{cid}"
        _ditto_delete_thing(thing_id)

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=5)
        client.loop_start()
        time.sleep(0.2)

        for car_id in car_ids:
            sentinel = json.dumps({"car_id": car_id, "_test_cleanup": True})
            # Evict position_processor state (subscribes to cars/raw_updates/+).
            client.publish(f"cars/raw_updates/{car_id}", sentinel, qos=1)
            # Evict detector state (each detector subscribes to cars/updates/+).
            client.publish(f"cars/updates/{car_id}", sentinel, qos=1)
            # Tell the traffic_jam_detector to evict this car from any jam
            # (the _test_cleanup sentinel on cars/updates already triggers
            # traffic_jam_detector._cleanup_car which removes the car from
            # jams and publishes traffic_jam_cleared with the proper jam_id
            # if the jam dissolves).

        # Give services time to process cleanup messages
        time.sleep(1.0)
        client.loop_stop()
        client.disconnect()
    except Exception as e:
        print(f"[cleanup] warning: mqtt cleanup failed: {e}")

    for car_id in car_ids:
        car_file = SIM_DIR / "devices" / f"{car_id}.json"
        if car_file.exists():
            try:
                car_file.unlink()
            except Exception as e:
                print(f"[cleanup] warning: failed to delete {car_file}: {e}")

    print(f"[cleanup] done for {len(car_ids)} cars")
