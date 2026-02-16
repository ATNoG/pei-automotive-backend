#
# Position Processor - service.py
#
# receives raw gps data from ditto_client.py
# calculates speed and heading given previous states
# and publishes the new car data updates to a MQTT broker
#
# also resolves the speed limit for the current road segment
# using the tile-cached Overpass road data
# so the frontend never needs to call external APIs itself.
#
from __future__ import annotations
import time
import logging
import sys
import threading
from pathlib import Path
from typing import Dict, Tuple, Optional

# add parent dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from common.logging_config import setup_logging
from common.config import load_config
from common.models import CarUpdate
from common.mqtt_client import MQTTClient
from common.ditto_client import DittoWSClient
from common.utils import haversine_distance_m, bearing_deg
from common.overpass_client import get_road_info, DEFAULT_SPEED_LIMIT_KMH

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
        # state for each car (thread-safe access)
        self.states: Dict[str, Tuple[float, float, float]] = {}
        self.states_lock = threading.Lock()
        # Ditto WebSocket client
        self.ditto = DittoWSClient(
            ws_url=config.ditto_ws_url,
            username=config.ditto_username,
            password=config.ditto_password,
            on_gps_update=self._handle_raw_gps,
        )

    def _resolve_speed_limit(self, lat: float, lon: float) -> float:
        """
        Look up the speed limit for the road nearest to (lat, lon).

        Uses the tile-cached Overpass data via get_road_info, which also
        returns the OSM way id and highway type for richer logging.
        Always returns a valid positive float.
        """
        try:
            speed_limit, way_id, hw_type = get_road_info(lat, lon)
            if way_id is not None:
                logger.debug(
                    "Speed limit %.0f km/h from way %d (%s) near (%.5f, %.5f)",
                    speed_limit, way_id, hw_type, lat, lon,
                )
            else:
                logger.debug(
                    "No road matched near (%.5f, %.5f), using default %.0f km/h",
                    lat, lon, speed_limit,
                )
            return speed_limit
        except Exception as e:
            logger.warning("Speed-limit lookup failed: %s", e)
            return float(DEFAULT_SPEED_LIMIT_KMH)

    def _handle_raw_gps(self, car_id: str, lat: float, lon: float):
        now = time.time()
        
        # Thread-safe state access
        with self.states_lock:
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

        # resolve speed limit for the current road segment
        speed_limit = self._resolve_speed_limit(lat, lon)

        # build enriched CarUpdate
        update = CarUpdate(
            car_id=car_id,
            latitude=lat,
            longitude=lon,
            speed_kmh=speed_kmh,
            heading_deg=heading,
            speed_limit_kmh=speed_limit,
            timestamp=now,
        )

        logger.info(
            "[PROC] %s: lat=%.6f, lon=%.6f, speed=%s, heading=%s, speed_limit=%.0f",
            car_id, lat, lon, speed_kmh, heading, speed_limit,
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