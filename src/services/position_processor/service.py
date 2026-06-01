#
# Position Processor
#
# Pure MQTT service: subscribes to cars/raw_updates/+ (published by the
# proximity_filter), computes speed, heading, and speed limit from consecutive
# GPS positions, then republishes an enriched CarUpdate to cars/updates/<car_id>
# for all detectors to consume.
#
# The tile_quadkey and tile_zoom fields injected by the proximity_filter are
# passed through unchanged so detectors can bucket state by tile.
#
from __future__ import annotations
import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from common.logging_config import setup_logging
from common.config import load_config
from common.models import CarUpdate
from common.mqtt_client import MQTTClient
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
        self.states: Dict[str, Tuple[float, float, float, float | None]] = {}
        self.states_lock = threading.Lock()

    def _resolve_speed_limit(self, lat: float, lon: float) -> float:
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

    def _handle_raw_update(self, payload: str) -> None:
        pp_rx_ts = time.time()

        try:
            data = json.loads(payload)
        except Exception as e:
            logger.error("Failed to parse raw update: %s", e)
            return

        car_id = data.get("car_id")
        if not car_id:
            logger.warning("Dropping update without car_id")
            return

        lat = data.get("latitude")
        lon = data.get("longitude")

        # Cleanup sentinel: evict state and forward so
        # all detectors on cars/updates can clean up their own state.
        if data.get("_test_cleanup"):
            with self.states_lock:
                if car_id in self.states:
                    del self.states[car_id]
                    logger.info("[CLEANUP] Removed car state: %s", car_id)
            self.mqtt.publish(
                f"{self.config.car_updates_topic}/{car_id}",
                json.dumps({"car_id": car_id, "_test_cleanup": True}),
                qos=1,
            )
            return

        lat = float(lat)
        lon = float(lon)
        emergency = bool(data.get("emergency", False))
        tile_quadkey = data.get("tile_quadkey")
        tile_zoom = data.get("tile_zoom")
        pf_ts = data.get("pf_ts")

        now = time.time()

        with self.states_lock:
            last = self.states.get(car_id)
            speed_kmh = None
            heading = None

            if last is not None:
                last_lat, last_lon, last_ts, last_heading = last
                dt = now - last_ts

                if dt > 0.05:
                    dist_m = haversine_distance_m(last_lat, last_lon, lat, lon)
                    speed_mps = dist_m / dt
                    speed_kmh = speed_mps * 3.6

                    if speed_kmh > 600 or speed_kmh < 0:
                        speed_kmh = None

                    if dist_m > 1.0:
                        heading = bearing_deg(last_lat, last_lon, lat, lon)
                    else:
                        heading = last_heading
                else:
                    heading = last_heading

            self.states[car_id] = (lat, lon, now, heading)

        speed_limit = self._resolve_speed_limit(lat, lon)

        update = CarUpdate(
            car_id=car_id,
            latitude=lat,
            longitude=lon,
            speed_kmh=speed_kmh,
            heading_deg=heading,
            speed_limit_kmh=speed_limit,
            emergency=emergency,
            tile_quadkey=tile_quadkey,
            tile_zoom=tile_zoom,
            timestamp=time.time(),
            pf_ts=pf_ts,
            pp_rx_ts=pp_rx_ts,
        )

        logger.info(
            "[PROC] %s: lat=%.6f, lon=%.6f, speed=%s, heading=%s, speed_limit=%.0f",
            car_id, lat, lon, speed_kmh, heading, speed_limit,
        )

        self.mqtt.publish(
            topic=f"{self.config.car_updates_topic}/{car_id}",
            payload=update.to_json(),
            qos=1,
        )

    def run(self):
        logger.info("Starting PositionProcessor...")
        self.mqtt.connect()
        self.mqtt.subscribe(f"{self.config.raw_car_updates_topic}/+", self._handle_raw_update)
        self.mqtt.loop_forever()


def main():
    setup_logging("position-processor")
    config = load_config()
    service = PositionProcessor(config)
    service.run()

if __name__ == "__main__":
    main()
