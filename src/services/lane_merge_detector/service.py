#
# Lane Merge Detector
# Detects whether it is safe or unsafe for a car to merge into a lane
# by predicting potential collisions based on current speeds and positions.
# Handles highway on-ramps, zip merges, and any lane convergence point.
#
# Supports multiple merge zones, each defined by a (main_lane, merging_lane)
# route pair. Zones come from /app/roads/merge_zones.json; if that file is
# absent the detector falls back to the legacy single pair ("highway",
# "entering") so older deployments keep working.
#
from __future__ import annotations
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple, List, Set

# add parent dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from common.logging_config import setup_logging
from common.config import load_config
from common.mqtt_client import MQTTClient
from common.models import CarUpdate, AlertPriority
from common.utils import haversine_distance_m, bearing_deg


logger = logging.getLogger(__name__)


@dataclass
class MergeZone:
    name: str
    main_lane_coords: List[Tuple[float, float]]
    merging_coords: List[Tuple[float, float]]
    merge_point: Optional[Tuple[float, float]] = None
    main_lane_cars: Set[str] = field(default_factory=set)
    merging_cars: Set[str] = field(default_factory=set)
    alerted_pairs: Set[Tuple[str, str]] = field(default_factory=set)


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

        self.zones: List[MergeZone] = self._load_zones()
        if not self.zones:
            logger.error("No merge zones loaded - detector will not produce alerts")
        else:
            for z in self.zones:
                logger.info(f"Merge zone '{z.name}' merge_point={z.merge_point}")

    def _find_roads_file(self, filename: str) -> Optional[Path]:
        docker_path = Path("/app/roads") / filename
        local_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "simulations" / "roads" / filename
        )
        if docker_path.exists():
            return docker_path
        if local_path.exists():
            return local_path
        return None

    def _load_zones(self) -> List[MergeZone]:
        cfg_path = self._find_roads_file("merge_zones.json")
        if cfg_path:
            try:
                with open(cfg_path) as f:
                    cfg = json.load(f)
                pair_specs = [
                    (z["name"], z["main_route"], z["merging_route"])
                    for z in cfg["zones"]
                ]
            except Exception as e:
                logger.error(f"Failed to parse {cfg_path}: {e}; falling back to default zone")
                pair_specs = [("default", "highway", "entering")]
        else:
            pair_specs = [("default", "highway", "entering")]

        zones: List[MergeZone] = []
        for name, main_name, merging_name in pair_specs:
            main_coords = self._load_route(main_name)
            merging_coords = self._load_route(merging_name)
            if not main_coords or not merging_coords:
                logger.warning(
                    f"Skipping zone '{name}': missing routes "
                    f"(main={main_name}, merging={merging_name})"
                )
                continue
            zones.append(MergeZone(
                name=name,
                main_lane_coords=main_coords,
                merging_coords=merging_coords,
                merge_point=merging_coords[-1],
            ))
        return zones

    def _load_route(self, route_name: str) -> List[Tuple[float, float]]:
        """Load route coordinates from JSON file."""
        route_file = self._find_roads_file(f"{route_name}.json")
        if not route_file:
            logger.error(f"Route file not found for '{route_name}'")
            return []
        try:
            with open(route_file) as f:
                data = json.load(f)
                coords = data["features"][0]["geometry"]["coordinates"]
            return [(lat, lon) for lat, lon in coords]
        except Exception as e:
            logger.error(f"Failed to load route {route_name}: {e}")
            return []

    def _classify_car(self, update: CarUpdate, zone: MergeZone) -> Optional[str]:
        """Classify if car is on main lane or merging road within this zone."""
        merging_min_dist = float('inf')
        main_lane_min_dist = float('inf')

        merging_check_coords = (
            zone.merging_coords[:-3] if len(zone.merging_coords) > 3 else zone.merging_coords
        )
        for route_lat, route_lon in merging_check_coords:
            dist = haversine_distance_m(update.latitude, update.longitude, route_lat, route_lon)
            merging_min_dist = min(merging_min_dist, dist)

        for route_lat, route_lon in zone.main_lane_coords:
            dist = haversine_distance_m(update.latitude, update.longitude, route_lat, route_lon)
            main_lane_min_dist = min(main_lane_min_dist, dist)

        threshold_m = 30

        if merging_min_dist < threshold_m and merging_min_dist < main_lane_min_dist:
            return "merging"
        elif main_lane_min_dist < threshold_m:
            return "main_lane"

        return None

    def _distance_to_merge_point(self, lat: float, lon: float, zone: MergeZone) -> float:
        if not zone.merge_point:
            return float('inf')
        return haversine_distance_m(lat, lon, zone.merge_point[0], zone.merge_point[1])

    def _find_closest_point_on_route(self, lat: float, lon: float,
                                     route: List[Tuple[float, float]]) -> int:
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

    def _predict_collision(self, merging_car: CarUpdate, main_lane_car: CarUpdate,
                           zone: MergeZone) -> Tuple[bool, float, float]:
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
            merging_car.latitude, merging_car.longitude, zone.merging_coords
        )
        main_lane_idx = self._find_closest_point_on_route(
            main_lane_car.latitude, main_lane_car.longitude, zone.main_lane_coords
        )

        merge_idx_on_main = self._find_closest_point_on_route(
            zone.merge_point[0], zone.merge_point[1], zone.main_lane_coords
        ) if zone.merge_point else len(zone.main_lane_coords) - 1

        min_distance = current_distance
        time_to_min_distance = 0.0

        for t in range(1, int(self.PREDICTION_TIME_S * 10)):  # check every 0.1s
            t_sec = t / 10.0

            merging_travel_dist = merging_speed_ms * t_sec
            main_lane_travel_dist = main_lane_speed_ms * t_sec

            distance_to_merge_along_route = 0
            temp_idx = merging_idx
            while temp_idx < len(zone.merging_coords) - 1:
                seg_dist = haversine_distance_m(
                    zone.merging_coords[temp_idx][0], zone.merging_coords[temp_idx][1],
                    zone.merging_coords[temp_idx + 1][0], zone.merging_coords[temp_idx + 1][1]
                )
                distance_to_merge_along_route += seg_dist
                temp_idx += 1

            if merging_travel_dist < distance_to_merge_along_route:
                pred_merging_pos = self._simulate_position_along_route(
                    zone.merging_coords, merging_idx, merging_travel_dist
                )
            else:
                remaining_dist = merging_travel_dist - distance_to_merge_along_route
                pred_merging_pos = self._simulate_position_along_route(
                    zone.main_lane_coords, merge_idx_on_main, remaining_dist
                )

            pred_main_lane_pos = self._simulate_position_along_route(
                zone.main_lane_coords, main_lane_idx, main_lane_travel_dist
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

        for zone in self.zones:
            self._process_zone_update(update, zone)

    def _process_zone_update(self, update: CarUpdate, zone: MergeZone) -> None:
        car_type = self._classify_car(update, zone)

        if car_type == "merging":
            zone.merging_cars.add(update.car_id)
            zone.main_lane_cars.discard(update.car_id)
        elif car_type == "main_lane":
            zone.main_lane_cars.add(update.car_id)
            zone.merging_cars.discard(update.car_id)
            dist_to_merge = self._distance_to_merge_point(
                update.latitude, update.longitude, zone
            )
            if dist_to_merge > self.ENTRY_ZONE_M * 2:
                zone.alerted_pairs = {
                    pair for pair in zone.alerted_pairs
                    if update.car_id not in pair
                }

        if update.speed_kmh is None or update.heading_deg is None:
            return

        if update.speed_kmh == 0:
            return

        if car_type != "merging":
            return

        dist_to_merge = self._distance_to_merge_point(
            update.latitude, update.longitude, zone
        )
        if dist_to_merge >= self.MERGE_POINT_DETECTION_M:
            return

        logger.info(
            f"[MERGE DETECTION][{zone.name}] Car {update.car_id} approaching merge "
            f"point (distance: {dist_to_merge:.1f}m)"
        )

        found_main_lane_car_in_zone = False

        for main_car_id in zone.main_lane_cars:
            if main_car_id not in self.cars:
                continue

            main_car = self.cars[main_car_id]

            if main_car.speed_kmh is None or main_car.heading_deg is None:
                continue
            if main_car.speed_kmh == 0:
                continue

            dist_main_to_merge = self._distance_to_merge_point(
                main_car.latitude, main_car.longitude, zone
            )

            if dist_main_to_merge >= self.ENTRY_ZONE_M:
                continue

            found_main_lane_car_in_zone = True
            logger.info(
                f"[MERGE DETECTION][{zone.name}] Analyzing collision: merging "
                f"{update.car_id} vs main {main_car_id}, dist={dist_main_to_merge:.1f}m"
            )
            collision, ttc, min_dist = self._predict_collision(update, main_car, zone)

            pair_key = (update.car_id, main_car_id)
            if pair_key in zone.alerted_pairs:
                continue

            if collision:
                alert = {
                    "alert_type": "lane_merge_unsafe",
                    "merging_car_id": update.car_id,
                    "main_lane_car_id": main_car_id,
                    "merging_speed_kmh": update.speed_kmh,
                    "main_lane_speed_kmh": main_car.speed_kmh,
                    "predicted_min_distance_m": round(min_dist, 2),
                    "time_to_closest_approach_s": round(ttc, 2),
                    "status": "unsafe",
                    "timestamp": time.time(),
                    "latitude": update.latitude,
                    "longitude": update.longitude,
                    "priority": int(AlertPriority.HIGH),
                    "expiration_s": 2,
                    "zone": zone.name,
                }
                self.mqtt.publish(self.alert_topic, json.dumps(alert))
                logger.warning(
                    f"[LANE MERGE - UNSAFE][{zone.name}] Car {update.car_id} "
                    f"cannot safely merge - collision risk with {main_car_id}. "
                    f"Predicted min distance: {min_dist:.1f}m"
                )
            else:
                alert = {
                    "alert_type": "lane_merge_safe",
                    "merging_car_id": update.car_id,
                    "main_lane_car_id": main_car_id,
                    "merging_speed_kmh": update.speed_kmh,
                    "main_lane_speed_kmh": main_car.speed_kmh,
                    "predicted_min_distance_m": round(min_dist, 2),
                    "status": "safe",
                    "timestamp": time.time(),
                    "latitude": update.latitude,
                    "longitude": update.longitude,
                    "priority": int(AlertPriority.MEDIUM),
                    "expiration_s": 2,
                    "zone": zone.name,
                }
                self.mqtt.publish(self.alert_topic, json.dumps(alert))
                logger.info(
                    f"[LANE MERGE - SAFE][{zone.name}] Car {update.car_id} "
                    f"can safely merge. Min distance to {main_car_id}: {min_dist:.1f}m"
                )

            zone.alerted_pairs.add(pair_key)

        if not found_main_lane_car_in_zone:
            already_alerted_no_traffic = any(
                pair[0] == update.car_id for pair in zone.alerted_pairs
            )
            if already_alerted_no_traffic:
                return

            alert = {
                "alert_type": "lane_merge_safe",
                "merging_car_id": update.car_id,
                "main_lane_car_id": None,
                "merging_speed_kmh": update.speed_kmh,
                "main_lane_speed_kmh": None,
                "predicted_min_distance_m": None,
                "status": "safe",
                "timestamp": time.time(),
                "latitude": update.latitude,
                "longitude": update.longitude,
                "priority": int(AlertPriority.MEDIUM),
                "expiration_s": 2,
                "zone": zone.name,
            }
            self.mqtt.publish(self.alert_topic, json.dumps(alert))
            logger.info(
                f"[LANE MERGE - SAFE][{zone.name}] Car {update.car_id} "
                f"can safely merge - no main lane traffic in entry zone"
            )
            zone.alerted_pairs.add((update.car_id, "no-traffic"))

    def _cleanup_car(self, car_id: str):
        """Remove all state for a specific car across every zone."""
        if car_id in self.cars:
            del self.cars[car_id]
            logger.info(f"[CLEANUP] Removed car state: {car_id}")

        for zone in self.zones:
            zone.main_lane_cars.discard(car_id)
            zone.merging_cars.discard(car_id)
            pairs_to_remove = {
                pair for pair in zone.alerted_pairs if car_id in pair
            }
            zone.alerted_pairs -= pairs_to_remove
            if pairs_to_remove:
                logger.info(
                    f"[CLEANUP][{zone.name}] Removed {len(pairs_to_remove)} "
                    f"alert pairs for {car_id}"
                )

    def run(self):
        logger.info("Starting Lane Merge Detector...")
        self.mqtt.connect()
        self.mqtt.subscribe(self.config.car_updates_topic, self._on_car_update)
        self.mqtt.loop_forever()


def main():
    setup_logging("lane-merge-detector")
    config = load_config()
    detector = LaneMergeDetector(config)
    detector.run()


if __name__ == "__main__":
    main()
