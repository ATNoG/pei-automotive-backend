"""Pytest configuration for test suite."""
import json
import os
import time
import uuid
from pathlib import Path
from typing import List

import paho.mqtt.client as mqtt
import pytest

# Test configuration
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1884"))
SIM_DIR = Path(__file__).resolve().parent.parent / "simulations"


def pytest_addoption(parser):
    """Add custom command-line options to pytest."""
    parser.addoption(
        "--fixed-ids",
        action="store_true",
        default=False,
        help="Use fixed car IDs instead of random UUIDs (useful for frontend testing)",
    )


@pytest.fixture(scope="session")
def use_fixed_ids(request):
    """Fixture that returns whether to use fixed car IDs."""
    return request.config.getoption("--fixed-ids")


@pytest.fixture
def test_car_registry():
    """Track all cars created during a test for cleanup."""
    car_ids = []
    yield car_ids
    # Cleanup after test - happens even if test fails
    _cleanup_test_cars(car_ids)


@pytest.fixture
def get_car_id(use_fixed_ids, test_car_registry):
    """Fixture that returns a function to generate car IDs and track them for cleanup."""
    def _get_car_id(base_name: str) -> str:
        """Generate car ID: random UUID by default, or fixed name with --fixed-ids flag."""
        if use_fixed_ids:
            car_id = base_name
        else:
            car_id = f"{base_name}-{str(uuid.uuid4())[:8]}"
        
        # Register for cleanup
        test_car_registry.append(car_id)
        return car_id
    
    return _get_car_id


def _cleanup_test_cars(car_ids: List[str]) -> None:
    """
    Comprehensive cleanup of test cars:
    1. Send cleanup signals to services via MQTT
    2. Remove local device files
    3. Wait for services to process cleanup
    """
    if not car_ids:
        return
    
    print(f"\n[CLEANUP] Removing {len(car_ids)} test cars from services...")
    
    # Send cleanup messages to services
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=5)
        client.loop_start()
        time.sleep(0.2)  # Ensure connection established
        
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
        print(f"[CLEANUP] Warning: MQTT cleanup failed: {e}")
    
    # Remove local device files
    for car_id in car_ids:
        car_file = SIM_DIR / "devices" / f"{car_id}.json"
        if car_file.exists():
            try:
                car_file.unlink()
            except Exception as e:
                print(f"[CLEANUP] Warning: Failed to delete {car_file}: {e}")
    
    print(f"[CLEANUP] Cleanup complete for {len(car_ids)} cars")
