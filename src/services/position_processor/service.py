#
# Position Processor - service.py
#
# receives raw gps data from ditto_client.py
# calculates speed and heading given previous states
# and publishes the new car data updates to a MQTT broker
#
from __future__ import annotations
import time
import logging
import sys
from pathlib import Path
from typing import Dict, Tuple

# add parent dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from common.logging_config import setup_logging
from common.config import load_config
from common.models import CarUpdate
from common.mqtt_client import MQTTClient
from common.ditto_client import DittoWSClient
from common.utils import haversine_distance_m, bearing_deg

logger = logging.getLogger(__name__)


class PositionProcessor:
    def __init__(self, config):
        self.config = config
        self.mqtt = MQTTClient(
            host=config.broker_host,
            port=config.broker_port,
            username=config.broker_user,
            password=config.broker_password,
            client_id="position-processor",
        )
        # state for each car
        self.states: Dict[str, Tuple[float, float, float]] = {}
        # Ditto WebSocket client
        self.ditto = DittoWSClient(
            ws_url=config.ditto_ws_url,
            username=config.ditto_username,
            password=config.ditto_password,
            on_gps_update=self._handle_raw_gps,
        )

    def _handle_raw_gps(self, car_id: str, lat: float, lon: float):
        now = time.time()
        last = self.states.get(car_id)

        speed_kmh = None
        heading = None

        if last is not None:
            last_lat, last_lon, last_ts = last
            dt = now - last_ts

            if dt > 0.05:  # allow faster updates for realistic speed calculation (50ms)
                dist_m = haversine_distance_m(last_lat, last_lon, lat, lon)
                speed_mps = dist_m / dt
                speed_kmh = speed_mps * 3.6

                # filter unrealistic values
                if speed_kmh > 600 or speed_kmh < 0:
                    speed_kmh = None

                if dist_m > 1.0:
                    heading = bearing_deg(last_lat, last_lon, lat, lon)

        # update state
        self.states[car_id] = (lat, lon, now)

        # build enriched CarUpdate
        update = CarUpdate(
            car_id=car_id,
            latitude=lat,
            longitude=lon,
            speed_kmh=speed_kmh,
            heading_deg=heading,
            timestamp=now,
        )

        logger.info(
            f"[PROC] {car_id}: lat={lat:.6f}, lon={lon:.6f}, "
            f"speed={speed_kmh}, heading={heading}"
        )

        # publish to MQTT
        self.mqtt.publish(
            topic=self.config.car_updates_topic,
            payload=update.to_json(),
            qos=1,
        )

    def run(self):
        logger.info("Starting PositionProcessor...")
        self.mqtt.connect()
        self.mqtt.start_loop()
        self.ditto.run_forever()


def main():
    setup_logging("position-processor")
    config = load_config()
    service = PositionProcessor(config)
    service.run()

if __name__ == "__main__":
    main()
