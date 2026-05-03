#
# Station Assigner Service
# Assigns each car to its nearest weather station and publishes updates
# when the assignment changes (i.e., car moves closer to a different station)
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
from common.models import CarUpdate, Station
from common.geopy_utils import find_nearest_station

logger = logging.getLogger(__name__)


class StationAssigner:
    def __init__(self, config):
        self.config = config
        self.mqtt = MQTTClient(
            host=config.broker_host,
            port=config.broker_port,
            username=config.broker_user,
            password=config.broker_password,
            client_id="station-assigner",
        )

        # In-memory state
        self.stations: Dict[int, Station] = {}  # station_id -> Station
        self.car_assignments: Dict[str, int] = {}  # car_id -> station_id

    def _on_meteo_update(self, payload: str):
        """Handle meteo updates to refresh the list of available stations"""
        try:
            data = json.loads(payload)
            stations_list = data.get("stations", [])
            
            # Clear and rebuild stations dict
            self.stations.clear()
            
            for station_data in stations_list:
                try:
                    # Reconstruct Station from dict
                    station = Station.from_dict(station_data)
                    self.stations[station.station_id] = station
                except Exception as e:
                    logger.error(f"Failed to parse station data: {e}")
                    continue
            
            logger.info(f"Updated station list: {len(self.stations)} stations available")
            
        except Exception as e:
            logger.error(f"Failed to process meteo update: {e}")

    def _on_car_update(self, payload: str):
        """Handle car position updates and assign nearest station"""
        try:
            data = json.loads(payload)
            
            # Handle test cleanup
            if data.get("_test_cleanup"):
                car_id = data.get("car_id")
                if car_id:
                    self._cleanup_car(car_id)
                return
            
            update = CarUpdate.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to parse car update: {e}")
            return

        # Skip if no stations available yet
        if not self.stations:
            logger.warning(f"No stations available, cannot assign station for car {update.car_id}")
            return

        # Find nearest station
        nearest = find_nearest_station(
            car_lat=update.latitude,
            car_lon=update.longitude,
            stations=self.stations
        )

        if not nearest:
            logger.warning(f"Could not find nearest station for car {update.car_id}")
            return

        # Get current assignment
        current_assignment = self.car_assignments.get(update.car_id)

        # Only publish if assignment changed or is new
        if current_assignment != nearest.station_id:
            self.car_assignments[update.car_id] = nearest.station_id
            
            # Publish to per-car topic
            topic = f"{self.config.station_assignment_topic_base}/{update.car_id}"
            payload = {
                "car_id": update.car_id,
                "station": nearest.to_dict(),
                "timestamp": time.time(),
            }
            
            self.mqtt.publish(topic, json.dumps(payload), retain=True)
            
            logger.info(
                f"[ASSIGNMENT] Car {update.car_id} -> Station {nearest.station_id} "
                f"({nearest.location_name})"
            )

    def _cleanup_car(self, car_id: str):
        """Remove all state for a specific car (used for test cleanup)"""
        if car_id in self.car_assignments:
            del self.car_assignments[car_id]
            logger.info(f"[CLEANUP] Removed car assignment: {car_id}")

    def run(self):
        logger.info("Starting Station Assigner Service...")
        self.mqtt.connect()
        
        # Subscribe to both meteo updates and car updates
        self.mqtt.subscribe(self.config.meteo_updates_topic, self._on_meteo_update)
        self.mqtt.subscribe(self.config.car_updates_topic, self._on_car_update)
        
        logger.info(f"Subscribed to {self.config.meteo_updates_topic}")
        logger.info(f"Subscribed to {self.config.car_updates_topic}")
        logger.info(f"Will publish to {self.config.station_assignment_topic_base}/{{car_id}}")
        
        self.mqtt.loop_forever()


def main():
    setup_logging("station-assigner")
    config = load_config()
    assigner = StationAssigner(config)
    assigner.run()


if __name__ == "__main__":
    main()
