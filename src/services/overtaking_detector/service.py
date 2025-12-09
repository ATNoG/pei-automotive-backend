#
# Overtaking detector
# detects overtaking events,
# for this, some conditions have to be met:
#    - same heading (diff <30º)
#    - proximity threshold
#    - determine position (ahead/behind) and determine transition
#
from __future__ import annotations
import json
import logging
import sys
import time
import math
from pathlib import Path
from typing import Dict, Tuple

# add parent dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from common.logging_config import setup_logging
from common.config import load_config
from common.mqtt_client import MQTTClient
from common.models import CarUpdate
from common.utils import haversine_distance_m


logger = logging.getLogger(__name__)


class OvertakingDetector:
    PROXIMITY_M = 50  # meters
    HEADING_TOLERANCE_DEG = 30

    def __init__(self, config):
        self.config = config
        self.mqtt = MQTTClient(
            host=config.broker_host,
            port=config.broker_port,
            username=config.broker_user,
            password=config.broker_password,
            client_id="overtaking-detector",
        )

        self.cars: Dict[str, CarUpdate] = {}
        # Track pair transitions: (A, B) -> last_relative_sign (-1 or +1)
        self.relative_positions: Dict[Tuple[str, str], int] = {}
        self.alert_topic = "alerts/overtaking"

    @staticmethod
    def _projection_sign(ax, ay, bx, by, heading_deg) -> int:
        # determine if B is ahead or behind A based on their pos
        # return: +1 = B is ahead
        #         -1 = B is behind
        heading_rad = math.radians(heading_deg)
        hx, hy = math.sin(heading_rad), math.cos(heading_rad)

        vx, vy = bx - ax, by - ay

        dot = vx * hx + vy * hy

        return +1 if dot >= 0 else -1

    def _on_car_update(self, payload: str):
        try:
            update = CarUpdate.from_dict(json.loads(payload))
        except Exception as e:
            logger.error(f"Failed to parse car update: {e}")
            return

        # must have speed & heading to be considered
        if update.speed_kmh is None or update.heading_deg is None:
            return

        # save updated state
        self.cars[update.car_id] = update

        # compare against all other cars
        for other_id, other in self.cars.items():
            if other_id == update.car_id:
                continue

            # both cars must have valid headings
            if other.heading_deg is None or other.speed_kmh is None:
                continue

            # cars must be moving in similar direction
            heading_diff = abs(update.heading_deg - other.heading_deg)
            heading_diff = min(heading_diff, 360 - heading_diff)
            if heading_diff > self.HEADING_TOLERANCE_DEG:
                continue

            # distance check
            dist = haversine_distance_m(update.latitude, update.longitude,
                               other.latitude, other.longitude)
            if dist > self.PROXIMITY_M:
                continue

            # determine relative positions
            sign = self._projection_sign(
                update.longitude, update.latitude,
                other.longitude, other.latitude,
                update.heading_deg
            )

            key = (update.car_id, other_id)
            previous_sign = self.relative_positions.get(key)

            # if state existed before and flipped:
            #   -1 (behind) → +1 (ahead)
            if previous_sign == +1 and sign == -1:
                alert = {
                    "alert_type": "overtaking_event",
                    "overtaking_car_id": update.car_id,
                    "overtaken_car_id": other_id,
                    "speed_kmh": update.speed_kmh,
                    "timestamp": time.time(),
                    "latitude": update.latitude,
                    "longitude": update.longitude,
                }
                self.mqtt.publish(self.alert_topic, json.dumps(alert))
                logger.warning(f"[OVERTAKE] {update.car_id} overtook {other_id}")

            # save new relative sign
            self.relative_positions[key] = sign

    def run(self):
        logger.info("Starting Overtaking Detector...")
        self.mqtt.connect()
        self.mqtt.subscribe(self.config.car_updates_topic, self._on_car_update)
        self.mqtt.loop_forever()


def main():
    setup_logging("overtaking-detector")
    config = load_config()
    detector = OvertakingDetector(config)
    detector.run()


if __name__ == "__main__":
    main()
