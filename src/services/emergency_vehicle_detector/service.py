#
# Emergency Vehicle Detector
# detects if an emergency vehicle is within a 500m radius
# of a regular car and notifies the user
#
from __future__ import annotations
import json
import logging
import sys
import time
from pathlib import Path
from typing import Dict

# add parent dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from common.logging_config import setup_logging
from common.config import load_config
from common.mqtt_client import MQTTClient
from common.models import CarUpdate, AlertPriority, AlertMetadata
from common.utils import haversine_distance_m, bearing_deg

logger = logging.getLogger(__name__)


class EVDetector:
    PROXIMITY_M = 500  # meters

    def __init__(self, config):
        self.config = config
        self.mqtt = MQTTClient(
            host=config.broker_host,
            port=config.broker_port,
            username=config.broker_user,
            password=config.broker_password,
            client_id="ev-detector",
        )

        self.cars: Dict[str, CarUpdate] = {}
        self.alert_topic = "alerts/emergency_vehicle"

    def _on_car_update(self, payload: str):
        try:
            data = json.loads(payload)
            
            # Handle test cleanup
            if data.get("_test_cleanup"):
                car_id = data.get("car_id")
                if car_id:
                    self._cleanup_car(car_id)
                return
            
            update = CarUpdate.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to parse car update: {e}")
            return

        # save updated state
        self.cars[update.car_id] = update

        # only trigger alerts when an emergency vehicle updates its position
        if not update.emergency:
            return

        # check all regular cars in range
        for other_id, other in self.cars.items():
            if other_id == update.car_id:
                continue

            # only alert regular (non-emergency) cars about nearby emergency vehicles
            if other.emergency:
                continue

            # distance check
            dist = haversine_distance_m(
                update.latitude, update.longitude,
                other.latitude, other.longitude,
            )

            if dist <= self.PROXIMITY_M:
                # Determine if the EV is ahead or behind the regular car
                direction = self._compute_direction(other, update)

                alert = {
                    "alert_type": "emergency_vehicle_nearby",
                    "emergency_vehicle_id": update.car_id,
                    "regular_car_id": other_id,
                    "distance_m": round(dist, 2),
                    "direction": direction,
                    "ev_latitude": update.latitude,
                    "ev_longitude": update.longitude,
                    "ev_heading_deg": update.heading_deg,
                    "car_latitude": other.latitude,
                    "car_longitude": other.longitude,
                    "timestamp": time.time(),
                    # Priority-based event aggregation
                    "priority": int(AlertPriority.HIGH),
                    "expiration_s": 3,  # Alert valid for 3 seconds
                }
                self.mqtt.publish(self.alert_topic, json.dumps(alert))
                logger.warning(
                    f"[EV] Emergency vehicle {update.car_id} is {dist:.1f}m "
                    f"{direction} {other_id}"
                )

    def _cleanup_car(self, car_id: str):
        """Remove all state for a specific car (used for test cleanup)."""
        if car_id in self.cars:
            del self.cars[car_id]
            logger.info(f"[CLEANUP] Removed car state: {car_id}")

    def _compute_direction(self, regular_car: CarUpdate, ev: CarUpdate) -> str:
        """Determine if the emergency vehicle is ahead of or behind the regular car.

        Uses the regular car's heading and the bearing from the regular car
        to the EV.  If the angular difference is within ±90° of the car's
        heading the EV is ahead; otherwise it is behind.
        """
        if regular_car.heading_deg is None:
            return "nearby"

        brng = bearing_deg(
            regular_car.latitude, regular_car.longitude,
            ev.latitude, ev.longitude,
        )
        diff = (brng - regular_car.heading_deg + 360) % 360
        # diff in (0, 360): 0-90 or 270-360 means the EV is in front
        if diff <= 90 or diff >= 270:
            return "ahead"
        return "behind"

    def run(self):
        logger.info("Starting Emergency Vehicle Detector...")
        self.mqtt.connect()
        self.mqtt.subscribe(self.config.car_updates_topic, self._on_car_update)
        self.mqtt.loop_forever()


def main():
    setup_logging("ev-detector")
    config = load_config()
    detector = EVDetector(config)
    detector.run()


if __name__ == "__main__":
    main()
