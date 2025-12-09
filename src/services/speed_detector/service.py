#
# Speeding detector
# detects if the car is speeding
#
from __future__ import annotations
import json
import logging
import sys
import time
from pathlib import Path

# add parent dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from common.logging_config import setup_logging
from common.config import load_config
from common.mqtt_client import MQTTClient
from common.models import CarUpdate

logger = logging.getLogger(__name__)


class SpeedDetector:
    def __init__(self, config):
        self.config = config
        self.speed_limit = config.speed_limit_kmh

        # set local topic
        self.alert_topic = "alerts/speed"

        self.mqtt = MQTTClient(
            host=config.broker_host,
            port=config.broker_port,
            username=config.broker_user,
            password=config.broker_password,
            client_id="speed-detector",
        )

    def _on_car_update(self, payload: str):
        try:
            data = json.loads(payload)
            update = CarUpdate.from_dict(data)
        except Exception as e:
            logger.error(f"Error processing car update: {e}")
            return

        # ignore updates without speed
        if update.speed_kmh is None:
            return

        if update.speed_kmh > self.speed_limit:
            alert = {
                "alert_type": "speed_violation",
                "car_id": update.car_id,
                "current_speed_kmh": update.speed_kmh,
                "speed_limit_kmh": self.speed_limit,
                "latitude": update.latitude,
                "longitude": update.longitude,
                "timestamp": time.time(),
            }

            self.mqtt.publish(self.alert_topic, json.dumps(alert))
            logger.warning(
                f"[SPEED] {update.car_id} speeding: {update.speed_kmh:.1f} km/h > {self.speed_limit}"
            )

    def run(self):
        logger.info("Starting Speed Detector...")
        self.mqtt.connect()
        self.mqtt.subscribe(self.config.car_updates_topic, self._on_car_update)
        self.mqtt.loop_forever()

def main():
    setup_logging("speed-detector")
    config = load_config()
    detector = SpeedDetector(config)
    detector.run()


if __name__ == "__main__":
    main()
