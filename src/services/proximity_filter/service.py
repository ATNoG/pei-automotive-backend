#
# Proximity Filter
#
# Sits transparently in front of the detector-facing cars/updates topic.
# Every car update flows through this service; no detector has to be aware
# of geotiles to benefit from per-tile reasoning later on.
#
#                              cars/raw_updates
#         position_processor ─────────────────▶ proximity_filter
#                                                       │ inject tile_quadkey
#                                                       │ + tile_zoom
#                                                       ▼
#                                                  cars/updates
#                                                       │
#                                ┌─────────┬────────────┼────────────┬─────────┐
#                                ▼         ▼            ▼            ▼         ▼
#                         overtaking  speed_detector  accident  highway_entry  ...
#
# The injected `tile_quadkey` (computed at PROXIMITY_ZOOM via Igor's QuadTree
# encoding) is the hook detectors use to bucket their state per tile and stop
# evaluating cars outside their proximity scope.
#
from __future__ import annotations
import json
import logging
import os
import sys
from pathlib import Path

# add parent dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from common.logging_config import setup_logging
from common.config import load_config
from common.mqtt_client import MQTTClient
from common.geotile import get_quadkey

logger = logging.getLogger(__name__)


class ProximityFilter:
    def __init__(self, config, proximity_zoom: int):
        self.config = config
        self.proximity_zoom = proximity_zoom
        self.mqtt = MQTTClient(
            host=config.broker_host,
            port=config.broker_port,
            username=config.broker_user,
            password=config.broker_password,
            client_id="proximity-filter",
        )

    def _enrich_and_forward(self, payload: str) -> None:
        try:
            data = json.loads(payload)
        except Exception as e:
            logger.error("Failed to parse car update: %s", e)
            return

        # Test cleanup sentinels and origin (0,0) markers pass through unchanged
        # so detectors still receive their cleanup signal on cars/updates.
        if not data.get("_test_cleanup"):
            lat = data.get("latitude")
            lon = data.get("longitude")
            if lat is not None and lon is not None and not (
                abs(float(lat)) < 0.0001 and abs(float(lon)) < 0.0001
            ):
                data["tile_quadkey"] = get_quadkey(
                    float(lat), float(lon), self.proximity_zoom
                )
                data["tile_zoom"] = self.proximity_zoom

        car_id = data.get("car_id")
        if not car_id:
            logger.warning("Dropping update without car_id: %s", payload[:120])
            return

        self.mqtt.publish(f"{self.config.car_updates_topic}/{car_id}", json.dumps(data))

    def run(self):
        logger.info(
            "Starting ProximityFilter: %s -> %s (zoom=%d)",
            self.config.raw_car_updates_topic,
            self.config.car_updates_topic,
            self.proximity_zoom,
        )
        self.mqtt.connect()
        self.mqtt.subscribe(self.config.raw_car_updates_topic, self._enrich_and_forward)
        self.mqtt.loop_forever()


def main():
    setup_logging("proximity-filter")
    config = load_config()
    proximity_zoom = int(os.getenv("PROXIMITY_ZOOM", "15"))
    service = ProximityFilter(config, proximity_zoom)
    service.run()


if __name__ == "__main__":
    main()
