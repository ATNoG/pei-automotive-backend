#
# Lane Merge Detector
# Detects whether it is safe or unsafe for a car to merge into a lane
# by predicting potential collisions based on current speeds and positions.
# Handles highway on-ramps, zip merges, and any lane convergence point.
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
from common.models import CarUpdate, AlertPriority
from common.utils import haversine_distance_m, bearing_deg


logger = logging.getLogger(__name__)


class LaneMergeDetector:
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
            client_id="lane-merge-detector",
        )

        self.cars: Dict[str, CarUpdate] = {}
        self.alert_topic = "alerts/lane_merge"

        # Track which cars are on the main lane vs merging
        self.main_lane_cars = set()
        self.merging_cars = set()

        # Load main lane and merging road coordinates
        self.main_lane_coords = self._load_route("highway")
        self.merging_coords = self._load_route("entering")

        # Find the merge point (where merging lane meets main lane)
        self.merge_point = self._find_merge_point()
        logger.info(f"Merge point identified at: {self.merge_point}")

        # Track last alert status per pair to avoid duplicate alerts.
        # Maps (merging_car_id, main_lane_car_id) -> "safe" | "unsafe".
        # A "safe" verdict can be upgraded to "unsafe" if a later evaluation
        # (e.g. with fresher main-lane position) detects a collision; an
        # "unsafe" verdict is sticky and is not downgraded back to "safe".
        self.alerted_pairs: Dict[Tuple[str, str], str] = {}

    def _load_route(self, route_name: str) -> List[Tuple[float, float]]:
        """Load route coordinates from JSON file."""
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
            return [(lat, lon) for lat, lon in coords]
        except Exception as e:
            logger.error(f"Failed to load route {route_name}: {e}")
            return []

    def _find_merge_point(self) -> Optional[Tuple[float, float]]:
        """Find the point where merging road meets main lane (end of merging road)."""
        if not self.merging_coords:
            return None
        return self.merging_coords[-1]

    def _is_near_route(self, lat: float, lon: float, route: List[Tuple[float, float]],
                       threshold_m: float = 30) -> bool:
        """Check if a position is near any point in the route."""
        for route_lat, route_lon in route:
            dist = haversine_distance_m(lat, lon, route_lat, route_lon)
            if dist < threshold_m:
                return True
        return False

    def _classify_car(self, update: CarUpdate) -> Optional[str]:
        """Classify if car is on main lane or merging road."""
        merging_min_dist = float('inf')
        main_lane_min_dist = float('inf')

        merging_check_coords = self.merging_coords[:-3] if len(self.merging_coords) > 3 else self.merging_coords
        for route_lat, route_lon in merging_check_coords:
            dist = haversine_distance_m(update.latitude, update.longitude, route_lat, route_lon)
            merging_min_dist = min(merging_min_dist, dist)

        for route_lat, route_lon in self.main_lane_coords:
            dist = haversine_distance_m(update.latitude, update.longitude, route_lat, route_lon)
            main_lane_min_dist = min(main_lane_min_dist, dist)

        threshold_m = 30

        if merging_min_dist < threshold_m and merging_min_dist < main_lane_min_dist:
            return "merging"
        elif main_lane_min_dist < threshold_m:
            return "main_lane"

        return None

    def _distance_to_merge_point(self, lat: float, lon: float) -> float:
        """Calculate distance to merge point."""
        if not self.merge_point:
            return float('inf')
        return haversine_distance_m(lat, lon, self.merge_point[0], self.merge_point[1])

    def _find_closest_point_on_route(self, lat: float, lon: float, route: List[Tuple[float, float]]) -> int:
        """Find the index of the closest point on a route to the given position."""
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
                ratio = remaining_distance / segment_distance
                final_lat = current_lat + (next_lat - current_lat) * ratio
                final_lon = current_lon + (next_lon - current_lon) * ratio
                return (final_lat, final_lon)
            else:
                remaining_distance -= segment_distance
                current_idx += 1

        return route[-1]

    def _predict_collision(self, merging_car: CarUpdate, main_lane_car: CarUpdate) -> Tuple[bool, float, float]:
        """
        Predict if a collision would occur by simulating both cars along their actual routes.
        The merging car follows the merging route until merge, then follows the main lane route.
        The main lane car continues along the main lane route.

        Returns:
            (collision_detected, time_to_collision, closest_distance)
        """
        merging_speed_ms = (merging_car.speed_kmh or 0) / 3.6
        main_lane_speed_ms = (main_lane_car.speed_kmh or 0) / 3.6

        current_distance = haversine_distance_m(
            merging_car.latitude, merging_car.longitude,
            main_lane_car.latitude, main_lane_car.longitude
        )

        if current_distance < self.COLLISION_THRESHOLD_M:
            return True, 0.0, current_distance

        merging_idx = self._find_closest_point_on_route(
            merging_car.latitude, merging_car.longitude, self.merging_coords
        )
        main_lane_idx = self._find_closest_point_on_route(
            main_lane_car.latitude, main_lane_car.longitude, self.main_lane_coords
        )

        merge_idx_on_main = self._find_closest_point_on_route(
            self.merge_point[0], self.merge_point[1], self.main_lane_coords
        ) if self.merge_point else len(self.main_lane_coords) - 1

        min_distance = current_distance
        time_to_min_distance = 0.0

        for t in range(1, int(self.PREDICTION_TIME_S * 10)):  # check every 0.1s
            t_sec = t / 10.0

            merging_travel_dist = merging_speed_ms * t_sec
            main_lane_travel_dist = main_lane_speed_ms * t_sec

            distance_to_merge_along_route = 0
            temp_idx = merging_idx
            while temp_idx < len(self.merging_coords) - 1:
                seg_dist = haversine_distance_m(
                    self.merging_coords[temp_idx][0], self.merging_coords[temp_idx][1],
                    self.merging_coords[temp_idx + 1][0], self.merging_coords[temp_idx + 1][1]
                )
                distance_to_merge_along_route += seg_dist
                temp_idx += 1

            if merging_travel_dist < distance_to_merge_along_route:
                pred_merging_pos = self._simulate_position_along_route(
                    self.merging_coords, merging_idx, merging_travel_dist
                )
            else:
                remaining_dist = merging_travel_dist - distance_to_merge_along_route
                pred_merging_pos = self._simulate_position_along_route(
                    self.main_lane_coords, merge_idx_on_main, remaining_dist
                )

            pred_main_lane_pos = self._simulate_position_along_route(
                self.main_lane_coords, main_lane_idx, main_lane_travel_dist
            )

            if pred_merging_pos and pred_main_lane_pos:
                pred_distance = haversine_distance_m(
                    pred_merging_pos[0], pred_merging_pos[1],
                    pred_main_lane_pos[0], pred_main_lane_pos[1]
                )

                if pred_distance < min_distance:
                    min_distance = pred_distance
                    time_to_min_distance = t_sec

        collision = min_distance < self.COLLISION_THRESHOLD_M

        return collision, time_to_min_distance, min_distance

    def _on_car_update(self, payload: str):
        try:
            data = json.loads(payload)

            if data.get("_test_cleanup"):
                car_id = data.get("car_id")
                if car_id:
                    self._cleanup_car(car_id)
                return

            update = CarUpdate.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to parse car update: {e}")
            return

        self.cars[update.car_id] = update

        car_type = self._classify_car(update)

        if car_type == "merging":
            self.merging_cars.add(update.car_id)
            self.main_lane_cars.discard(update.car_id)
        elif car_type == "main_lane":
            self.main_lane_cars.add(update.car_id)
            self.merging_cars.discard(update.car_id)
            dist_to_merge = self._distance_to_merge_point(update.latitude, update.longitude)
            if dist_to_merge > self.ENTRY_ZONE_M * 2:
                self.alerted_pairs = {
                    pair: status for pair, status in self.alerted_pairs.items()
                    if update.car_id not in pair
                }

        if update.speed_kmh is None or update.heading_deg is None:
            return

        if update.speed_kmh == 0:
            return

        # Re-evaluate pairs on EVERY relevant update — whether the merging car or
        # the main-lane car just moved. The collision verdict depends on both
        # positions, and updates can arrive out of order; gating evaluation on
        # only one side would lock in a stale verdict.
        if car_type == "merging":
            self._evaluate_merging_car(update)
        elif car_type == "main_lane":
            self._evaluate_main_lane_car(update)

    def _evaluate_merging_car(self, merging_update: CarUpdate) -> None:
        dist_to_merge = self._distance_to_merge_point(
            merging_update.latitude, merging_update.longitude
        )
        if dist_to_merge >= self.MERGE_POINT_DETECTION_M:
            return

        logger.info(
            f"[MERGE DETECTION] Car {merging_update.car_id} is approaching merge point "
            f"(distance: {dist_to_merge:.1f}m)"
        )

        found_main_lane_car_in_zone = False
        for main_car_id in list(self.main_lane_cars):
            main_car = self.cars.get(main_car_id)
            if not self._is_evaluable(main_car):
                continue
            if self._distance_to_merge_point(main_car.latitude, main_car.longitude) < self.ENTRY_ZONE_M:
                found_main_lane_car_in_zone = True
                self._evaluate_pair(merging_update, main_car)

        if not found_main_lane_car_in_zone:
            self._maybe_publish_no_traffic(merging_update)

    def _evaluate_main_lane_car(self, main_update: CarUpdate) -> None:
        if self._distance_to_merge_point(main_update.latitude, main_update.longitude) >= self.ENTRY_ZONE_M:
            return

        for merging_car_id in list(self.merging_cars):
            merging_car = self.cars.get(merging_car_id)
            if not self._is_evaluable(merging_car):
                continue
            dist_to_merge = self._distance_to_merge_point(
                merging_car.latitude, merging_car.longitude
            )
            if dist_to_merge < self.MERGE_POINT_DETECTION_M:
                self._evaluate_pair(merging_car, main_update)

    @staticmethod
    def _is_evaluable(car: Optional[CarUpdate]) -> bool:
        return (
            car is not None
            and car.speed_kmh is not None
            and car.heading_deg is not None
            and car.speed_kmh > 0
        )

    def _evaluate_pair(self, merging_car: CarUpdate, main_car: CarUpdate) -> None:
        logger.info(
            f"[MERGE DETECTION] Analyzing collision: merging {merging_car.car_id} "
            f"vs main {main_car.car_id}"
        )
        collision, ttc, min_dist = self._predict_collision(merging_car, main_car)
        pair_key = (merging_car.car_id, main_car.car_id)
        prev_status = self.alerted_pairs.get(pair_key)

        if collision:
            if prev_status == "unsafe":
                return
            alert = {
                "alert_type": "lane_merge_unsafe",
                "merging_car_id": merging_car.car_id,
                "main_lane_car_id": main_car.car_id,
                "merging_speed_kmh": merging_car.speed_kmh,
                "main_lane_speed_kmh": main_car.speed_kmh,
                "predicted_min_distance_m": round(min_dist, 2),
                "time_to_closest_approach_s": round(ttc, 2),
                "status": "unsafe",
                "timestamp": time.time(),
                "latitude": merging_car.latitude,
                "longitude": merging_car.longitude,
                "priority": int(AlertPriority.HIGH),
                "expiration_s": 2,
            }
            payload = json.dumps(alert)
            self.mqtt.publish(f"{self.alert_topic}/{merging_car.car_id}", payload)
            self.mqtt.publish(f"{self.alert_topic}/{main_car.car_id}", payload)
            logger.warning(
                f"[LANE MERGE - UNSAFE] Car {merging_car.car_id} cannot safely merge - "
                f"collision risk with {main_car.car_id}. Predicted min distance: {min_dist:.1f}m"
            )
            self.alerted_pairs[pair_key] = "unsafe"
        else:
            if prev_status is not None:
                return
            alert = {
                "alert_type": "lane_merge_safe",
                "merging_car_id": merging_car.car_id,
                "main_lane_car_id": main_car.car_id,
                "merging_speed_kmh": merging_car.speed_kmh,
                "main_lane_speed_kmh": main_car.speed_kmh,
                "predicted_min_distance_m": round(min_dist, 2),
                "status": "safe",
                "timestamp": time.time(),
                "latitude": merging_car.latitude,
                "longitude": merging_car.longitude,
                "priority": int(AlertPriority.MEDIUM),
                "expiration_s": 2,
            }
            self.mqtt.publish(f"{self.alert_topic}/{merging_car.car_id}", json.dumps(alert))
            logger.info(
                f"[LANE MERGE - SAFE] Car {merging_car.car_id} can safely merge. "
                f"Min distance to {main_car.car_id}: {min_dist:.1f}m"
            )
            self.alerted_pairs[pair_key] = "safe"

    def _maybe_publish_no_traffic(self, merging_update: CarUpdate) -> None:
        if merging_update.car_id in [pair[0] for pair in self.alerted_pairs]:
            return
        alert = {
            "alert_type": "lane_merge_safe",
            "merging_car_id": merging_update.car_id,
            "main_lane_car_id": None,
            "merging_speed_kmh": merging_update.speed_kmh,
            "main_lane_speed_kmh": None,
            "predicted_min_distance_m": None,
            "status": "safe",
            "timestamp": time.time(),
            "latitude": merging_update.latitude,
            "longitude": merging_update.longitude,
            "priority": int(AlertPriority.MEDIUM),
            "expiration_s": 2,
        }
        self.mqtt.publish(f"{self.alert_topic}/{merging_update.car_id}", json.dumps(alert))
        logger.info(
            f"[LANE MERGE - SAFE] Car {merging_update.car_id} "
            f"can safely merge - no main lane traffic in entry zone"
        )
        self.alerted_pairs[(merging_update.car_id, "no-traffic")] = "safe"

    def _cleanup_car(self, car_id: str):
        """Remove all state for a specific car (used for test cleanup)."""
        if car_id in self.cars:
            del self.cars[car_id]
            logger.info(f"[CLEANUP] Removed car state: {car_id}")

        self.main_lane_cars.discard(car_id)
        self.merging_cars.discard(car_id)

        pairs_to_remove = [
            pair for pair in self.alerted_pairs
            if car_id in pair
        ]
        for pair in pairs_to_remove:
            del self.alerted_pairs[pair]
        if pairs_to_remove:
            logger.info(f"[CLEANUP] Removed {len(pairs_to_remove)} alert pairs for {car_id}")

    def run(self):
        logger.info("Starting Lane Merge Detector...")
        self.mqtt.connect()
        self.mqtt.subscribe(f"{self.config.car_updates_topic}/+", self._on_car_update)
        self.mqtt.loop_forever()


def main():
    setup_logging("lane-merge-detector")
    config = load_config()
    detector = LaneMergeDetector(config)
    detector.run()


if __name__ == "__main__":
    main()
