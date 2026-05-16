#
# Proximity Filter
#
# Sits between the Ditto gateway and the detector-facing cars/updates topic.
# Enriches every car update with tile_quadkey + tile_zoom computed from the
# car's lat/lon via Igor's QuadTree encoding, then republishes per-car:
#
#   cars/raw_updates/<car_id>  →  proximity_filter  →  cars/updates/<car_id>
#
# Cleanup sentinels (_test_cleanup) and origin-marker updates (lat≈0, lon≈0)
# pass through unchanged so detectors can clean their state.
#
from __future__ import annotations
import json
import logging
import os
import sys
from pathlib import Path

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

        car_id = data.get("car_id")
        if not car_id:
            logger.warning("Dropping update without car_id: %s", payload[:120])
            return

        lat = data.get("latitude")
        lon = data.get("longitude")

        is_sentinel = data.get("_test_cleanup") or (
            lat is not None and lon is not None
            and abs(float(lat)) < 0.0001 and abs(float(lon)) < 0.0001
        )

        if not is_sentinel and lat is not None and lon is not None:
            data["tile_quadkey"] = get_quadkey(float(lat), float(lon), self.proximity_zoom)
            data["tile_zoom"] = self.proximity_zoom

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
