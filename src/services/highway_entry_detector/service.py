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
    COLLISION_THRESHOLD_M = 20  # minimum safe distance (meters)
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
                data = json.load(f)
                coords = data["features"][0]["geometry"]["coordinates"]
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
        # Calculate minimum distance to each route (excluding the merge point area)
        # This helps distinguish between routes near the merge point
        
        entering_min_dist = float('inf')
        highway_min_dist = float('inf')
        
        # For entering road, skip the last few points (near merge) for classification
        entering_check_coords = self.entering_coords[:-3] if len(self.entering_coords) > 3 else self.entering_coords
        for route_lat, route_lon in entering_check_coords:
            dist = haversine_distance_m(update.latitude, update.longitude, route_lat, route_lon)
            entering_min_dist = min(entering_min_dist, dist)
        
        # For highway, check all points
        for route_lat, route_lon in self.highway_coords:
            dist = haversine_distance_m(update.latitude, update.longitude, route_lat, route_lon)
            highway_min_dist = min(highway_min_dist, dist)
        
        # Use relative distance comparison - car belongs to the closest route
        # But only if within reasonable threshold
        threshold_m = 30
        
        if entering_min_dist < threshold_m and entering_min_dist < highway_min_dist:
            return "entering"
        elif highway_min_dist < threshold_m:
            return "highway"
        
        return None

    def _distance_to_merge_point(self, lat: float, lon: float) -> float:
        """Calculate distance to merge point"""
        if not self.merge_point:
            return float('inf')
        return haversine_distance_m(lat, lon, self.merge_point[0], self.merge_point[1])

    def _find_closest_point_on_route(self, lat: float, lon: float, route: List[Tuple[float, float]]) -> int:
        """Find the index of the closest point on a route to the given position"""
        if not route:
            return 0
        
        min_dist = float('inf')
        closest_idx = 0
        
        for i, (route_lat, route_lon) in enumerate(route):
            dist = haversine_distance_m(lat, lon, route_lat, route_lon)
            if dist < min_dist:
                min_dist = dist
                closest_idx = i
        
        return closest_idx
    
    def _simulate_position_along_route(self, route: List[Tuple[float, float]], 
                                       start_idx: int, distance_m: float) -> Optional[Tuple[float, float]]:
        """
        Simulate moving along a route from start_idx for distance_m meters.
        Returns the predicted (lat, lon) position.
        """
        if not route or start_idx >= len(route):
            return None
        
        current_idx = start_idx
        remaining_distance = distance_m
        
        while current_idx < len(route) - 1 and remaining_distance > 0:
            current_lat, current_lon = route[current_idx]
            next_lat, next_lon = route[current_idx + 1]
            
            segment_distance = haversine_distance_m(current_lat, current_lon, next_lat, next_lon)
            
            if segment_distance >= remaining_distance:
                # Interpolate within this segment
                ratio = remaining_distance / segment_distance
                final_lat = current_lat + (next_lat - current_lat) * ratio
                final_lon = current_lon + (next_lon - current_lon) * ratio
                return (final_lat, final_lon)
            else:
                # Move to next segment
                remaining_distance -= segment_distance
                current_idx += 1
        
        # Reached end of route, return last point
        return route[-1]

    def _predict_collision(self, entering_car: CarUpdate, highway_car: CarUpdate) -> Tuple[bool, float, float]:
        """
        Predict if a collision would occur by simulating both cars along their actual routes.
        The entering car follows the entering route until merge, then follows the highway route.
        The highway car continues along the highway route.
        
        Returns:
            (collision_detected, time_to_collision, closest_distance)
        """
        # Get speeds in m/s
        entering_speed_ms = (entering_car.speed_kmh or 0) / 3.6
        highway_speed_ms = (highway_car.speed_kmh or 0) / 3.6
        
        # Calculate current distance
        current_distance = haversine_distance_m(
            entering_car.latitude, entering_car.longitude, 
            highway_car.latitude, highway_car.longitude
        )
        
        # If already too close, it's unsafe
        if current_distance < self.COLLISION_THRESHOLD_M:
            return True, 0.0, current_distance
        
        # Find current positions on their respective routes
        entering_idx = self._find_closest_point_on_route(
            entering_car.latitude, entering_car.longitude, self.entering_coords
        )
        highway_idx = self._find_closest_point_on_route(
            highway_car.latitude, highway_car.longitude, self.highway_coords
        )
        
        # Find merge point index on highway route
        merge_idx_on_highway = self._find_closest_point_on_route(
            self.merge_point[0], self.merge_point[1], self.highway_coords
        ) if self.merge_point else len(self.highway_coords) - 1
        
        min_distance = current_distance
        time_to_min_distance = 0.0
        
        # Simulate future positions over time window
        for t in range(1, int(self.PREDICTION_TIME_S * 10)):  # check every 0.1s
            t_sec = t / 10.0
            
            # Distance each car travels
            entering_travel_dist = entering_speed_ms * t_sec
            highway_travel_dist = highway_speed_ms * t_sec
            
            # Simulate entering car: follows entering route, then highway route after merge
            distance_to_merge_along_route = 0
            temp_idx = entering_idx
            while temp_idx < len(self.entering_coords) - 1:
                seg_dist = haversine_distance_m(
                    self.entering_coords[temp_idx][0], self.entering_coords[temp_idx][1],
                    self.entering_coords[temp_idx + 1][0], self.entering_coords[temp_idx + 1][1]
                )
                distance_to_merge_along_route += seg_dist
                temp_idx += 1
            
            if entering_travel_dist < distance_to_merge_along_route:
                # Still on entering route
                pred_entering_pos = self._simulate_position_along_route(
                    self.entering_coords, entering_idx, entering_travel_dist
                )
            else:
                # Past merge point, now on highway
                remaining_dist = entering_travel_dist - distance_to_merge_along_route
                pred_entering_pos = self._simulate_position_along_route(
                    self.highway_coords, merge_idx_on_highway, remaining_dist
                )
            
            # Simulate highway car: continues along highway route
            pred_highway_pos = self._simulate_position_along_route(
                self.highway_coords, highway_idx, highway_travel_dist
            )
            
            # Calculate distance between predicted positions
            if pred_entering_pos and pred_highway_pos:
                pred_distance = haversine_distance_m(
                    pred_entering_pos[0], pred_entering_pos[1],
                    pred_highway_pos[0], pred_highway_pos[1]
                )
                
                if pred_distance < min_distance:
                    min_distance = pred_distance
                    time_to_min_distance = t_sec
        
        # Collision detected if minimum distance is below threshold
        collision = min_distance < self.COLLISION_THRESHOLD_M
        
        return collision, time_to_min_distance, min_distance

    def _on_car_update(self, payload: str):
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

        # Save updated state (even without speed/heading for tracking)
        self.cars[update.car_id] = update
        
        # Classify the car and track in appropriate set (regardless of speed/heading)
        car_type = self._classify_car(update)
        
        if car_type == "entering":
            self.entering_cars.add(update.car_id)
            self.highway_cars.discard(update.car_id)
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
        
        # For collision analysis, we need speed and heading
        if update.speed_kmh is None or update.heading_deg is None:
            return
        
        # Skip collision analysis if speed is zero (stationary cars)
        if update.speed_kmh == 0:
            return

        # Only analyze entering cars for collision detection
        if car_type == "entering":
            # Check if car is approaching merge point
            dist_to_merge = self._distance_to_merge_point(update.latitude, update.longitude)
            
            if dist_to_merge < self.MERGE_POINT_DETECTION_M:
                logger.info(f"[ENTRY DETECTION] Car {update.car_id} is approaching merge point (distance: {dist_to_merge:.1f}m)")
                
                # Track if we found any highway cars in detection zone
                found_highway_car_in_zone = False
                
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
                        found_highway_car_in_zone = True
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
                                
                                self.mqtt.publish(f"{self.alert_topic}/{update.car_id}", json.dumps(alert))
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
                                
                                self.mqtt.publish(f"{self.alert_topic}/{update.car_id}", json.dumps(alert))
                                logger.info(
                                    f"[HIGHWAY ENTRY - SAFE] Car {update.car_id} "
                                    f"can safely merge. Min distance to {highway_car_id}: {min_dist:.1f}m"
                                )
                                self.alerted_pairs.add(pair_key)
                
                # If no highway cars found in detection zone, it's safe to enter
                if not found_highway_car_in_zone:
                    # Only alert once per entering car
                    if update.car_id not in [pair[0] for pair in self.alerted_pairs]:
                        alert = {
                            "alert_type": "highway_entry_safe",
                            "entering_car_id": update.car_id,
                            "highway_car_id": None,
                            "entering_speed_kmh": update.speed_kmh,
                            "highway_speed_kmh": None,
                            "predicted_min_distance_m": None,
                            "status": "safe",
                            "timestamp": time.time(),
                            "latitude": update.latitude,
                            "longitude": update.longitude,
                        }
                        
                        self.mqtt.publish(f"{self.alert_topic}/{update.car_id}", json.dumps(alert))
                        logger.info(
                            f"[HIGHWAY ENTRY - SAFE] Car {update.car_id} "
                            f"can safely merge - no highway traffic detected in entry zone"
                        )
                        # Mark this entering car as alerted
                        self.alerted_pairs.add((update.car_id, "no-traffic"))

    def _cleanup_car(self, car_id: str):
        """Remove all state for a specific car (used for test cleanup)."""
        # Remove from cars dictionary
        if car_id in self.cars:
            del self.cars[car_id]
            logger.info(f"[CLEANUP] Removed car state: {car_id}")
        
        # Remove from classification sets
        self.highway_cars.discard(car_id)
        self.entering_cars.discard(car_id)
        
        # Remove from alerted pairs
        pairs_to_remove = {
            pair for pair in self.alerted_pairs 
            if car_id in pair
        }
        self.alerted_pairs -= pairs_to_remove
        if pairs_to_remove:
            logger.info(f"[CLEANUP] Removed {len(pairs_to_remove)} alert pairs for {car_id}")

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
