#
# Highway Entry Detector
# Detects whether it is safe or unsafe for a car to enter the highway
# by predicting potential collisions based on current speeds and positions.
#
from __future__ import annotations
import json
import logging
import sys
import time
import math
from pathlib import Path
from typing import Dict, Optional, Tuple, List

# add parent dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from common.logging_config import setup_logging
from common.config import load_config
from common.mqtt_client import MQTTClient
from common.models import CarUpdate
from common.utils import haversine_distance_m, bearing_deg


logger = logging.getLogger(__name__)


class HighwayEntryDetector:
    # Detection parameters
    ENTRY_ZONE_M = 100  # distance to consider entry zone (meters)
    MERGE_POINT_DETECTION_M = 20  # distance to merge point to trigger analysis
    COLLISION_THRESHOLD_M = 10  # minimum safe distance (meters)
    PREDICTION_TIME_S = 5  # time window for prediction (seconds)
    
    def __init__(self, config):
        self.config = config
        self.mqtt = MQTTClient(
            host=config.broker_host,
            port=config.broker_port,
            username=config.broker_user,
            password=config.broker_password,
            client_id="highway-entry-detector",
        )

        self.cars: Dict[str, CarUpdate] = {}
        self.alert_topic = "alerts/highway_entry"
        
        # Track which cars are on highway vs entering
        self.highway_cars = set()  # cars on the highway
        self.entering_cars = set()  # cars trying to enter
        
        # Load highway and entering road coordinates
        self.highway_coords = self._load_route("highway")
        self.entering_coords = self._load_route("entering")
        
        # Find the merge point (where entering lane meets highway)
        self.merge_point = self._find_merge_point()
        logger.info(f"Merge point identified at: {self.merge_point}")
        
        # Track already alerted pairs to avoid duplicate alerts
        self.alerted_pairs = set()

    def _load_route(self, route_name: str) -> List[Tuple[float, float]]:
        """Load route coordinates from JSON file"""
        # Try Docker path first, then local development path
        docker_path = Path("/app/roads") / f"{route_name}.json"
        local_path = Path(__file__).resolve().parent.parent.parent.parent / "simulations" / "roads" / f"{route_name}.json"
        
        if docker_path.exists():
            route_file = docker_path
        elif local_path.exists():
            route_file = local_path
        else:
            logger.error(f"Route file not found. Tried: {docker_path} and {local_path}")
            return []
        
        try:
            with open(route_file) as f:
                coords = json.load(f)
            # Convert to (lat, lon) tuples
            return [(lat, lon) for lat, lon in coords]
        except Exception as e:
            logger.error(f"Failed to load route {route_name}: {e}")
            return []

    def _find_merge_point(self) -> Optional[Tuple[float, float]]:
        """Find the point where entering road meets highway (end of entering road)"""
        if not self.entering_coords:
            return None
        # The merge point is the last point of the entering road
        return self.entering_coords[-1]

    def _is_near_route(self, lat: float, lon: float, route: List[Tuple[float, float]], 
                       threshold_m: float = 30) -> bool:
        """Check if a position is near any point in the route"""
        for route_lat, route_lon in route:
            dist = haversine_distance_m(lat, lon, route_lat, route_lon)
            if dist < threshold_m:
                return True
        return False

    def _classify_car(self, update: CarUpdate) -> Optional[str]:
        """Classify if car is on highway or entering road"""
        # Check entering road first with higher priority
        # Use tighter threshold for entering to distinguish from highway at merge point
        is_near_entering = self._is_near_route(
            update.latitude, update.longitude, 
            self.entering_coords, threshold_m=15
        )
        
        if is_near_entering:
            return "entering"
        
        # Check if near highway (use larger threshold)
        is_near_highway = self._is_near_route(
            update.latitude, update.longitude, 
            self.highway_coords, threshold_m=25
        )
        
        if is_near_highway:
            return "highway"
        
        return None

    def _distance_to_merge_point(self, lat: float, lon: float) -> float:
        """Calculate distance to merge point"""
        if not self.merge_point:
            return float('inf')
        return haversine_distance_m(lat, lon, self.merge_point[0], self.merge_point[1])

    def _predict_collision(self, entering_car: CarUpdate, highway_car: CarUpdate) -> Tuple[bool, float, float]:
        """
        Predict if a collision would occur if entering car merges now.
        
        Returns:
            (collision_detected, time_to_collision, closest_distance)
        """
        # Get current positions
        entering_lat, entering_lon = entering_car.latitude, entering_car.longitude
        highway_lat, highway_lon = highway_car.latitude, highway_car.longitude
        
        # Get speeds in m/s
        entering_speed_ms = (entering_car.speed_kmh or 0) / 3.6
        highway_speed_ms = (highway_car.speed_kmh or 0) / 3.6
        
        # Calculate current distance
        current_distance = haversine_distance_m(
            entering_lat, entering_lon, 
            highway_lat, highway_lon
        )
        
        # If already too close, it's unsafe
        if current_distance < self.COLLISION_THRESHOLD_M:
            return True, 0.0, current_distance
        
        # Predict future positions over time window
        min_distance = current_distance
        time_to_min_distance = 0.0
        
        # Simulate both cars moving along their headings
        for t in range(1, int(self.PREDICTION_TIME_S * 10)):  # check every 0.1s
            t_sec = t / 10.0
            
            # Predict positions based on heading and speed
            if entering_car.heading_deg is not None:
                # Calculate new position for entering car
                # Heading: 0° = North, 90° = East, 180° = South, 270° = West
                entering_heading_rad = math.radians(entering_car.heading_deg)
                entering_dist = entering_speed_ms * t_sec
                
                # Convert distance to lat/lon offset
                # North component (latitude): cos(heading) * distance
                # East component (longitude): sin(heading) * distance
                # 1 degree lat ≈ 111km, 1 degree lon ≈ 111km * cos(lat)
                entering_lat_offset = (entering_dist * math.cos(entering_heading_rad)) / 111000
                entering_lon_offset = (entering_dist * math.sin(entering_heading_rad)) / (111000 * math.cos(math.radians(entering_lat)))
                
                pred_entering_lat = entering_lat + entering_lat_offset
                pred_entering_lon = entering_lon + entering_lon_offset
            else:
                pred_entering_lat = entering_lat
                pred_entering_lon = entering_lon
            
            if highway_car.heading_deg is not None:
                # Calculate new position for highway car
                highway_heading_rad = math.radians(highway_car.heading_deg)
                highway_dist = highway_speed_ms * t_sec
                
                highway_lat_offset = (highway_dist * math.cos(highway_heading_rad)) / 111000
                highway_lon_offset = (highway_dist * math.sin(highway_heading_rad)) / (111000 * math.cos(math.radians(highway_lat)))
                
                pred_highway_lat = highway_lat + highway_lat_offset
                pred_highway_lon = highway_lon + highway_lon_offset
            else:
                pred_highway_lat = highway_lat
                pred_highway_lon = highway_lon
            
            # Calculate distance at this time
            pred_distance = haversine_distance_m(
                pred_entering_lat, pred_entering_lon,
                pred_highway_lat, pred_highway_lon
            )
            
            if pred_distance < min_distance:
                min_distance = pred_distance
                time_to_min_distance = t_sec
        
        # Collision detected if minimum distance is below threshold
        collision = min_distance < self.COLLISION_THRESHOLD_M
        
        return collision, time_to_min_distance, min_distance

    def _on_car_update(self, payload: str):
        try:
            update = CarUpdate.from_dict(json.loads(payload))
        except Exception as e:
            logger.error(f"Failed to parse car update: {e}")
            return

        # Save updated state (even without speed/heading for tracking)
        self.cars[update.car_id] = update
        
        # Must have speed and heading to be analyzed
        if update.speed_kmh is None or update.heading_deg is None:
            return
        
        # Skip if speed is zero (stationary cars)
        if update.speed_kmh == 0:
            return

        # Classify the car
        car_type = self._classify_car(update)
        
        if car_type == "entering":
            self.entering_cars.add(update.car_id)
            self.highway_cars.discard(update.car_id)
            
            # Check if car is approaching merge point
            dist_to_merge = self._distance_to_merge_point(update.latitude, update.longitude)
            
            if dist_to_merge < self.MERGE_POINT_DETECTION_M:
                logger.info(f"[ENTRY DETECTION] Car {update.car_id} is approaching merge point (distance: {dist_to_merge:.1f}m)")
                
                # Check for potential collisions with highway cars
                for highway_car_id in self.highway_cars:
                    if highway_car_id not in self.cars:
                        continue
                    
                    highway_car = self.cars[highway_car_id]
                    
                    # Highway car must also have speed and heading
                    if highway_car.speed_kmh is None or highway_car.heading_deg is None:
                        continue
                    
                    # Skip stationary highway cars
                    if highway_car.speed_kmh == 0:
                        continue
                    
                    # Check if highway car is in the entry zone
                    dist_highway_to_merge = self._distance_to_merge_point(
                        highway_car.latitude, highway_car.longitude
                    )
                    
                    if dist_highway_to_merge < self.ENTRY_ZONE_M:
                        logger.info(f"[ENTRY DETECTION] Analyzing collision: entering {update.car_id} vs highway {highway_car_id}, dist={dist_highway_to_merge:.1f}m")
                        # Predict collision
                        collision, ttc, min_dist = self._predict_collision(update, highway_car)
                        
                        pair_key = (update.car_id, highway_car_id)
                        
                        if collision:
                            # Only alert once per pair per entry attempt
                            if pair_key not in self.alerted_pairs:
                                alert = {
                                    "alert_type": "highway_entry_unsafe",
                                    "entering_car_id": update.car_id,
                                    "highway_car_id": highway_car_id,
                                    "entering_speed_kmh": update.speed_kmh,
                                    "highway_speed_kmh": highway_car.speed_kmh,
                                    "predicted_min_distance_m": round(min_dist, 2),
                                    "time_to_closest_approach_s": round(ttc, 2),
                                    "status": "unsafe",
                                    "timestamp": time.time(),
                                    "latitude": update.latitude,
                                    "longitude": update.longitude,
                                }
                                
                                self.mqtt.publish(self.alert_topic, json.dumps(alert))
                                logger.warning(
                                    f"[HIGHWAY ENTRY - UNSAFE] Car {update.car_id} "
                                    f"cannot safely merge - collision risk with {highway_car_id}. "
                                    f"Predicted min distance: {min_dist:.1f}m"
                                )
                                self.alerted_pairs.add(pair_key)
                        else:
                            # Safe to merge
                            if pair_key not in self.alerted_pairs:
                                alert = {
                                    "alert_type": "highway_entry_safe",
                                    "entering_car_id": update.car_id,
                                    "highway_car_id": highway_car_id,
                                    "entering_speed_kmh": update.speed_kmh,
                                    "highway_speed_kmh": highway_car.speed_kmh,
                                    "predicted_min_distance_m": round(min_dist, 2),
                                    "status": "safe",
                                    "timestamp": time.time(),
                                    "latitude": update.latitude,
                                    "longitude": update.longitude,
                                }
                                
                                self.mqtt.publish(self.alert_topic, json.dumps(alert))
                                logger.info(
                                    f"[HIGHWAY ENTRY - SAFE] Car {update.car_id} "
                                    f"can safely merge. Min distance to {highway_car_id}: {min_dist:.1f}m"
                                )
                                self.alerted_pairs.add(pair_key)
        
        elif car_type == "highway":
            self.highway_cars.add(update.car_id)
            self.entering_cars.discard(update.car_id)
            # Remove from alerted pairs when car moves away from merge zone
            dist_to_merge = self._distance_to_merge_point(update.latitude, update.longitude)
            if dist_to_merge > self.ENTRY_ZONE_M * 2:
                # Clean up old alerts for this car
                self.alerted_pairs = {
                    pair for pair in self.alerted_pairs 
                    if update.car_id not in pair
                }

    def run(self):
        logger.info("Starting Highway Entry Detector...")
        self.mqtt.connect()
        self.mqtt.subscribe(self.config.car_updates_topic, self._on_car_update)
        self.mqtt.loop_forever()


def main():
    setup_logging("highway-entry-detector")
    config = load_config()
    detector = HighwayEntryDetector(config)
    detector.run()


if __name__ == "__main__":
    main()
