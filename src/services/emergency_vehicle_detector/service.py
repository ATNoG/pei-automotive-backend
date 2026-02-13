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
from common.models import CarUpdate
from common.utils import haversine_distance_m

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
            update = CarUpdate.from_dict(json.loads(payload))
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
                alert = {
                    "alert_type": "emergency_vehicle_nearby",
                    "emergency_vehicle_id": update.car_id,
                    "regular_car_id": other_id,
                    "distance_m": round(dist, 2),
                    "ev_latitude": update.latitude,
                    "ev_longitude": update.longitude,
                    "car_latitude": other.latitude,
                    "car_longitude": other.longitude,
                    "timestamp": time.time(),
                }
                self.mqtt.publish(self.alert_topic, json.dumps(alert))
                logger.warning(
                    f"[EV] Emergency vehicle {update.car_id} is {dist:.1f}m "
                    f"from {other_id}"
                )

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
