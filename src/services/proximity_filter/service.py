#
# Proximity Filter
#
# Replaces the position_processor as the entry point for Ditto car events.
# Connects to Ditto WebSocket, enriches every GPS update with a tile_quadkey
# (computed via Igor's QuadTree encoding at PROXIMITY_ZOOM), and publishes
# a lightweight raw message to MQTT for the position_processor to consume.
#
#   Ditto WS  →  proximity_filter  →  cars/raw_updates/<car_id>  →  position_processor
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
from common.ditto_client import DittoWSClient
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
        self.ditto = DittoWSClient(
            ws_url=config.ditto_ws_url,
            username=config.ditto_username,
            password=config.ditto_password,
            on_gps_update=self._on_gps_update,
        )

    def _on_gps_update(self, car_id: str, lat: float, lon: float, emergency: bool = False) -> None:
        tile_qk = get_quadkey(lat, lon, self.proximity_zoom)
        payload = json.dumps({
            "car_id": car_id,
            "latitude": lat,
            "longitude": lon,
            "emergency": emergency,
            "tile_quadkey": tile_qk,
            "tile_zoom": self.proximity_zoom,
        })
        self.mqtt.publish(f"{self.config.raw_car_updates_topic}/{car_id}", payload)

    def run(self):
        logger.info(
            "Starting ProximityFilter: Ditto WS -> %s (zoom=%d)",
            self.config.raw_car_updates_topic,
            self.proximity_zoom,
        )
        self.mqtt.connect()
        self.mqtt.start_loop()
        self.ditto.run_forever()


def main():
    setup_logging("proximity-filter")
    config = load_config()
    proximity_zoom = int(os.getenv("PROXIMITY_ZOOM", "15"))
    service = ProximityFilter(config, proximity_zoom)
    service.run()


if __name__ == "__main__":
    main()
