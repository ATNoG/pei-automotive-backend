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
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

from helpers import MQTT_HOST, MQTT_PORT, SIM_DIR, make_mqtt_client

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
        return car_id

    return _get_car_id


def _cleanup_test_cars(car_ids: List[str]) -> None:
    """clean up test cars from services and local device files."""
    if not car_ids:
        return

    print(f"\n[cleanup] removing {len(car_ids)} test cars...")

    try:
        client = make_mqtt_client()
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=5)
        client.loop_start()
        time.sleep(0.2)

        for car_id in car_ids:
            # Send cleanup signal to all services
            cleanup_msg = json.dumps({
                "action": "cleanup",
                "car_id": car_id,
                "timestamp": time.time()
            })
            client.publish(f"test/cleanup/{car_id}", cleanup_msg, qos=1)

            # Tell consumers to clear stale per-car traffic jam alerts.
            clear_msg = json.dumps({
                "notification_type": "traffic_jam_clear",
                "target_car_id": car_id,
                "reason": "test_cleanup",
                "timestamp": time.time()
            })
            client.publish(f"alerts/traffic_jam/{car_id}", clear_msg, qos=1)
            
            # Also send a final position update with special marker to trigger state cleanup
            # This ensures the car is removed from all service states
            cleanup_update = json.dumps({
                "car_id": car_id,
                "latitude": 0.0,
                "longitude": 0.0,
                "speed_kmh": None,
                "heading_deg": None,
                "speed_limit_kmh": 50.0,
                "emergency": False,
                "timestamp": time.time(),
                "_test_cleanup": True
            })
            client.publish("cars/updates", cleanup_update, qos=1)
            
        # Give services time to process cleanup messages
        time.sleep(0.3)
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
