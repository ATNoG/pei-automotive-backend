#
# Proximity Filter
#
# Sits transparently in front of the detector-facing cars/updates topic.
# Every car update flows through this service; no detector has to be aware
# of geotiles to benefit from per-tile reasoning.
#
#                              cars/raw_updates
#         position_processor ─────────────────▶ proximity_filter
#                                                       │ inject tile_quadkey
#                                                       │ + tile_zoom
#                                                       ▼
#                                                  cars/updates          (all detectors)
#                                                  cars/in_scope         (multi-car detectors only)
#
# cars/updates  — enriched copy of every update; single-car detectors
#                 (speed, highway_entry, accident, ev) subscribe here.
# cars/in_scope — published only when a tile contains ≥2 known cars;
#                 multi-car detectors (overtaking, traffic_jam) subscribe
#                 here so they skip lone-tile updates they can never act on.
#
# Cleanup sentinels (_test_cleanup) and origin-marker updates (lat≈0, lon≈0)
# are forwarded to BOTH topics so all subscribers can clean their state.
#
from __future__ import annotations
import json
import logging
import os
import sys
from collections import defaultdict
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
        # tile_qk → set of car_ids currently in that tile
        self.cars_by_tile: dict[int, set[str]] = defaultdict(set)
        # car_id → current tile_qk
        self.car_tile: dict[str, int] = {}

    def _update_tile(self, car_id: str, tile_qk: int) -> None:
        old_tile = self.car_tile.get(car_id)
        if old_tile is not None and old_tile != tile_qk:
            self.cars_by_tile[old_tile].discard(car_id)
            if not self.cars_by_tile[old_tile]:
                del self.cars_by_tile[old_tile]
        self.cars_by_tile[tile_qk].add(car_id)
        self.car_tile[car_id] = tile_qk

    def _evict_car(self, car_id: str) -> None:
        old_tile = self.car_tile.pop(car_id, None)
        if old_tile is not None:
            self.cars_by_tile[old_tile].discard(car_id)
            if not self.cars_by_tile[old_tile]:
                del self.cars_by_tile[old_tile]

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

        # Cleanup sentinels: evict from tile tracking and forward to both
        # topics so all subscribers (including those on in_scope) can clean up.
        if data.get("_test_cleanup"):
            self._evict_car(car_id)
            serialized = json.dumps(data)
            self.mqtt.publish(f"{self.config.car_updates_topic}/{car_id}", serialized)
            self.mqtt.publish(f"{self.config.in_scope_topic}/{car_id}", serialized)
            return

        lat = data.get("latitude")
        lon = data.get("longitude")

        # Origin markers (lat≈0, lon≈0): same treatment as cleanup sentinels.
        if lat is not None and lon is not None and (
            abs(float(lat)) < 0.0001 and abs(float(lon)) < 0.0001
        ):
            self._evict_car(car_id)
            serialized = json.dumps(data)
            self.mqtt.publish(f"{self.config.car_updates_topic}/{car_id}", serialized)
            self.mqtt.publish(f"{self.config.in_scope_topic}/{car_id}", serialized)
            return

        # Normal update: enrich with tile_quadkey and maintain tile state.
        tile_qk = None
        if lat is not None and lon is not None:
            tile_qk = get_quadkey(float(lat), float(lon), self.proximity_zoom)
            data["tile_quadkey"] = tile_qk
            data["tile_zoom"] = self.proximity_zoom
            self._update_tile(car_id, tile_qk)

        serialized = json.dumps(data)
        self.mqtt.publish(f"{self.config.car_updates_topic}/{car_id}", serialized)

        # Publish to cars/in_scope only when the tile has ≥2 cars.
        if tile_qk is not None and len(self.cars_by_tile[tile_qk]) >= 2:
            self.mqtt.publish(f"{self.config.in_scope_topic}/{car_id}", serialized)

    def run(self):
        logger.info(
            "Starting ProximityFilter: %s -> %s / %s (zoom=%d)",
            self.config.raw_car_updates_topic,
            self.config.car_updates_topic,
            self.config.in_scope_topic,
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
