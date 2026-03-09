#
# Meteo Consumer - service.py
#
# Fetches meteorological data from Weather API (meteo:* things)
# and publishes updates to MQTT broker for frontend consumption
#
from __future__ import annotations
import time
import logging
import sys
import json
from pathlib import Path
from typing import List

# add parent dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.logging_config import setup_logging
from common.config import load_config
from common.models import Station
from common.mqtt_client import MQTTClient
from common.ditto_rest_client import DittoRestClient

logger = logging.getLogger(__name__)


class MeteoConsumer:
    def __init__(self, config):
        self.config = config
        self.mqtt = MQTTClient(
            host=config.broker_host,
            port=config.broker_port,
            username=config.broker_user,
            password=config.broker_password,
            client_id="meteo-consumer",
        )
        self.weather_client = DittoRestClient(
            api_url=config.weather_api_url,
            username=config.weather_username,
            password=config.weather_password,
        )
        self.poll_interval = 300  # Poll every 5 minutes (weather data doesn't change frequently)

    def fetch_and_publish_meteo_data(self):
        """Fetch all meteo things from Weather API and publish to MQTT"""
        try:
            logger.info(f"Fetching meteorological data from {self.config.weather_api_url}...")
            meteo_things = self.weather_client.get_all_meteo_things()
            
            if not meteo_things:
                logger.warning("No meteorological things found")
                return
            
            logger.info(f"Found {len(meteo_things)} meteorological stations")
            
            stations: List[Station] = []
            for thing_data in meteo_things:
                try:
                    station = Station.from_ditto_thing(thing_data)
                    stations.append(station)
                    logger.debug(
                        f"Parsed station {station.station_id}: {station.location_name}"
                    )
                except Exception as e:
                    logger.error(f"Error parsing meteo thing: {e}", exc_info=True)
                    continue
            
            # Publish all stations as a single message
            if stations:
                payload = {
                    "stations": [s.to_dict() for s in stations],
                    "timestamp": time.time(),
                    "count": len(stations),
                }
                
                self.mqtt.publish(
                    self.config.meteo_updates_topic,
                    json.dumps(payload)
                )
                
                logger.info(
                    f"Published {len(stations)} stations to {self.config.meteo_updates_topic}"
                )
                
        except Exception as e:
            logger.error(f"Error in fetch_and_publish_meteo_data: {e}", exc_info=True)

    def run(self):
        """Main loop: fetch and publish meteo data periodically"""
        logger.info("Meteo Consumer started")
        self.mqtt.connect()
        logger.info(f"Connected to MQTT broker at {self.config.broker_host}:{self.config.broker_port}")
        
        # Initial fetch
        self.fetch_and_publish_meteo_data()
        
        # Poll periodically
        while True:
            try:
                time.sleep(self.poll_interval)
                self.fetch_and_publish_meteo_data()
            except KeyboardInterrupt:
                logger.info("Shutting down meteo consumer...")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                # Wait a bit before retrying
                time.sleep(30)
        
        self.mqtt.disconnect()


def main():
    setup_logging()
    config = load_config()
    consumer = MeteoConsumer(config)
    consumer.run()


if __name__ == "__main__":
    main()
