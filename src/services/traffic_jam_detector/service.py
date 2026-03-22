#
# Traffic Jam Detector
# Detects traffic jams by identifying clusters of multiple cars (5+) in a line
# traveling at speeds well below the limit (< 30% of speed_limit_kmh)
# within a proximity radius (500m) in the same direction (±30° heading tolerance)
#
from __future__ import annotations
import json
import logging
import sys
import time
from pathlib import Path
from typing import Dict, Set, Optional, List, Tuple
from dataclasses import dataclass, field

# add parent dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from common.logging_config import setup_logging
from common.config import load_config
from common.mqtt_client import MQTTClient
from common.models import CarUpdate
from common.utils import haversine_distance_m, bearing_deg

logger = logging.getLogger(__name__)


@dataclass
class CarState:
    """Tracks a car's recent state for traffic jam detection."""
    car_id: str
    latitude: float
    longitude: float
    speed_kmh: Optional[float] = None
    heading_deg: Optional[float] = None
    speed_limit_kmh: Optional[float] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class TrafficJam:
    """
    Represents a detected traffic jam event.
    
    A jam is identified by a cluster of slow-moving vehicles
    in the same location traveling in the same direction.
    """
    jam_id: str
    car_ids: Set[str]
    center_latitude: float
    center_longitude: float
    detected_at: float
    last_updated: float
    last_alert_time: float = 0.0
    active: bool = True

    def to_dict(self) -> Dict:
        return {
            "jam_id": self.jam_id,
            "type": "traffic_jam",
            "car_ids": list(self.car_ids),
            "severity": len(self.car_ids),
            "center_latitude": self.center_latitude,
            "center_longitude": self.center_longitude,
            "detected_at": self.detected_at,
            "last_updated": self.last_updated,
            "active": self.active,
        }


class TrafficJamDetector:
    # Detection parameters
    MIN_CARS_FOR_JAM = 5  # minimum number of cars to qualify as a traffic jam
    SPEED_THRESHOLD_RATIO = 0.30  # cars must be traveling < 30% of speed limit
    PROXIMITY_RADIUS_M = 500  # cars must be within 500m to be in same jam
    HEADING_TOLERANCE_DEG = 30  # cars must be traveling within ±30° to be in same jam
    
    # Notification parameters
    NOTIFICATION_RADIUS_M = 2000  # notify cars within 2km of jam
    MIN_SPEED_FOR_NOTIFICATION = 10  # km/h - car must be moving to receive notification
    
    # State management
    JAM_EXPIRY_S = 300  # 5 minutes - expire jams without updates
    ALERT_COOLDOWN_S = 120  # 2 minutes - prevent duplicate alerts for same jam
    CAR_STATE_EXPIRY_S = 60  # expire car states older than 1 minute

    def __init__(self, config):
        self.config = config
        self.mqtt = MQTTClient(
            host=config.broker_host,
            port=config.broker_port,
            username=config.broker_user,
            password=config.broker_password,
            client_id="traffic-jam-detector",
        )

        self.cars: Dict[str, CarState] = {}
        self.active_jams: Dict[str, TrafficJam] = {}
        self.alert_topic = "alerts/traffic_jam"
        self.jam_counter = 0

    def _generate_jam_id(self, center_lat: float, center_lon: float) -> str:
        """
        Generate a jam ID based on geographic location.
        
        Rounds coordinates to 3 decimal places (~100m precision)
        to group nearby jams together.
        """
        lat_rounded = round(center_lat, 3)
        lon_rounded = round(center_lon, 3)
        return f"JAM-{lat_rounded:.3f},{lon_rounded:.3f}"

    def _calculate_center(self, car_states: List[CarState]) -> Tuple[float, float]:
        """Calculate the geographic center (centroid) of a cluster of cars."""
        if not car_states:
            return 0.0, 0.0
        
        avg_lat = sum(car.latitude for car in car_states) / len(car_states)
        avg_lon = sum(car.longitude for car in car_states) / len(car_states)
        return avg_lat, avg_lon

    def _is_within_heading_tolerance(
        self, heading1: Optional[float], heading2: Optional[float]
    ) -> bool:
        """
        Check if two headings are within the tolerance angle.
        
        Handles the circular nature of angles (e.g., 350° and 10° are 20° apart).
        """
        if heading1 is None or heading2 is None:
            return False

        heading_diff = abs(heading1 - heading2)
        heading_diff = min(heading_diff, 360 - heading_diff)
        
        return heading_diff <= self.HEADING_TOLERANCE_DEG

    def _is_car_slow(self, car: CarState) -> bool:
        """Check if a car is traveling slowly (below threshold)."""
        if car.speed_kmh is None or car.speed_limit_kmh is None:
            return False
        
        # Car must be traveling below 30% of the speed limit
        return car.speed_kmh < (self.SPEED_THRESHOLD_RATIO * car.speed_limit_kmh)

    def _find_jam_cluster(self, reference_car: CarState) -> List[CarState]:
        """
        Find all cars that form a jam cluster with the reference car.
        
        Returns a list of CarState objects that are:
        - Within proximity radius
        - Traveling in the same direction (heading tolerance)
        - Traveling slowly (below speed threshold)
        """
        cluster = []
        
        for car_id, car_state in self.cars.items():
            # Include the car if it meets all criteria
            if not self._is_car_slow(car_state):
                continue
            
            # Check proximity
            distance = haversine_distance_m(
                reference_car.latitude, reference_car.longitude,
                car_state.latitude, car_state.longitude
            )
            if distance > self.PROXIMITY_RADIUS_M:
                continue
            
            # Check heading alignment
            if not self._is_within_heading_tolerance(
                reference_car.heading_deg, car_state.heading_deg
            ):
                continue
            
            cluster.append(car_state)
        
        return cluster

    def _find_existing_jam(self, car_ids: Set[str]) -> Optional[TrafficJam]:
        """
        Find an existing jam that overlaps significantly with the given car IDs.
        
        Returns the jam if >50% of car IDs match any active jam.
        """
        for jam in self.active_jams.values():
            if not jam.active:
                continue
            
            # Calculate overlap
            overlap = len(car_ids & jam.car_ids)
            overlap_ratio = overlap / max(len(car_ids), len(jam.car_ids))
            
            if overlap_ratio > 0.5:
                return jam
        
        return None

    def _should_alert_for_jam(self, jam: TrafficJam) -> bool:
        """Check if we should send an alert for this jam (cooldown check)."""
        now = time.time()
        time_since_last_alert = now - jam.last_alert_time
        return time_since_last_alert >= self.ALERT_COOLDOWN_S

    def _is_jam_ahead(self, car: CarState, jam: TrafficJam) -> bool:
        """
        Determine if the traffic jam is AHEAD of the car.
        
        Uses the car's heading to check if the jam location
        is in front of it. Works for any road geometry.
        
        Returns True if jam is ahead, False if car already passed.
        """
        if car.heading_deg is None:
            return False

        # Bearing from car to jam center
        bearing_to_jam = bearing_deg(
            car.latitude, car.longitude,
            jam.center_latitude, jam.center_longitude
        )

        # Angular difference
        heading_diff = abs(car.heading_deg - bearing_to_jam)
        heading_diff = min(heading_diff, 360 - heading_diff)

        # <= 90° means jam is in front of or perpendicular to the car
        # > 90° means car has already passed the jam
        return heading_diff <= 90

    def _should_notify_car(self, car: CarState, jam: TrafficJam) -> bool:
        """
        Check if a car should receive a traffic jam notification.
        
        Conditions:
        1. Not already in the jam
        2. Within notification radius (2km)
        3. Moving (not stopped)
        4. Jam is AHEAD (not already passed)
        """
        # Skip cars already in the jam
        if car.car_id in jam.car_ids:
            return False

        # Car must be moving to receive notification
        if car.speed_kmh is None or car.speed_kmh < self.MIN_SPEED_FOR_NOTIFICATION:
            return False

        # Check distance
        dist = haversine_distance_m(
            car.latitude, car.longitude,
            jam.center_latitude, jam.center_longitude
        )
        if dist > self.NOTIFICATION_RADIUS_M:
            return False

        # Check if jam is ahead
        return self._is_jam_ahead(car, jam)

    def _notify_nearby_vehicles(self, jam: TrafficJam) -> int:
        """Notify vehicles approaching the jam. Returns count."""
        notified = 0

        for car_id, car_state in self.cars.items():
            if self._should_notify_car(car_state, jam):
                dist = haversine_distance_m(
                    car_state.latitude, car_state.longitude,
                    jam.center_latitude, jam.center_longitude
                )

                notification = {
                    "notification_type": "traffic_jam_alert",
                    "target_car_id": car_id,
                    "jam_id": jam.jam_id,
                    "jam": jam.to_dict(),
                    "distance_m": dist,
                    "timestamp": time.time(),
                }

                # Send to individual car and broadcast
                self.mqtt.publish(f"alerts/traffic_jam/{car_id}", json.dumps(notification))
                
                logger.info(f"[JAM NOTIFY] {car_id} - jam ahead at {dist:.0f}m")
                notified += 1

        return notified

    def _detect_traffic_jams(self):
        """
        Detect traffic jams by analyzing clusters of slow-moving cars.
        
        Called after each car update to check for new or updated jams.
        """
        now = time.time()
        processed_cars: Set[str] = set()
        
        # Check each slow-moving car for potential jam clusters
        for car_id, car_state in self.cars.items():
            if car_id in processed_cars:
                continue
            
            if not self._is_car_slow(car_state):
                continue
            
            # Find all cars in a jam cluster with this car
            cluster = self._find_jam_cluster(car_state)
            
            # Check if we have enough cars for a jam
            if len(cluster) < self.MIN_CARS_FOR_JAM:
                continue
            
            # Calculate cluster center
            center_lat, center_lon = self._calculate_center(cluster)
            
            # Get car IDs in this cluster
            cluster_car_ids = {car.car_id for car in cluster}
            
            # Mark these cars as processed
            processed_cars.update(cluster_car_ids)
            
            # Generate jam ID based on location
            jam_id = self._generate_jam_id(center_lat, center_lon)
            
            # Check if this jam already exists
            existing_jam = self._find_existing_jam(cluster_car_ids)
            
            if existing_jam:
                # Update existing jam
                existing_jam.car_ids = cluster_car_ids
                existing_jam.center_latitude = center_lat
                existing_jam.center_longitude = center_lon
                existing_jam.last_updated = now
                
                logger.debug(
                    f"[JAM UPDATE] {existing_jam.jam_id}: {len(cluster_car_ids)} cars"
                )
            else:
                # Create new jam
                new_jam = TrafficJam(
                    jam_id=jam_id,
                    car_ids=cluster_car_ids,
                    center_latitude=center_lat,
                    center_longitude=center_lon,
                    detected_at=now,
                    last_updated=now,
                )
                
                self.active_jams[jam_id] = new_jam
                
                logger.warning(
                    f"[TRAFFIC JAM DETECTED] {jam_id} at "
                    f"({center_lat:.6f}, {center_lon:.6f}) with {len(cluster_car_ids)} cars"
                )
                
                # Send alert for new jam
                if self._should_alert_for_jam(new_jam):
                    self._publish_jam_alert(new_jam)
                    # Notify nearby vehicles approaching the jam
                    notified = self._notify_nearby_vehicles(new_jam)
                    logger.info(f"[JAM NOTIFY] Notified {notified} vehicles approaching jam")
                    new_jam.last_alert_time = now

    def _publish_jam_alert(self, jam: TrafficJam):
        """Publish a traffic jam alert to the MQTT broker."""
        alert = {
            "alert_type": "traffic_jam",
            "jam_id": jam.jam_id,
            "car_ids": list(jam.car_ids),
            "severity": len(jam.car_ids),
            "center_latitude": jam.center_latitude,
            "center_longitude": jam.center_longitude,
            "detected_at": jam.detected_at,
            "timestamp": time.time(),
        }
        
        self.mqtt.publish(self.alert_topic, json.dumps(alert))
        
        logger.warning(
            f"[ALERT] Traffic jam at ({jam.center_latitude:.6f}, {jam.center_longitude:.6f}) "
            f"with {len(jam.car_ids)} vehicles"
        )

    def _publish_jam_cleared(self, jam: TrafficJam, reason: str):
        """Publish a jam-cleared event to let consumers clear stale UI alerts."""
        event = {
            "alert_type": "traffic_jam_cleared",
            "jam_id": jam.jam_id,
            "car_ids": list(jam.car_ids),
            "severity": len(jam.car_ids),
            "center_latitude": jam.center_latitude,
            "center_longitude": jam.center_longitude,
            "reason": reason,
            "timestamp": time.time(),
        }
        self.mqtt.publish(self.alert_topic, json.dumps(event))

    def _remove_jam_if_dissolved(self, jam_id: str, reason: str):
        """Remove jam if it no longer meets minimum size and publish clear event."""
        jam = self.active_jams.get(jam_id)
        if jam is None:
            return
        if len(jam.car_ids) >= self.MIN_CARS_FOR_JAM:
            return

        jam.active = False
        self._publish_jam_cleared(jam, reason)
        del self.active_jams[jam_id]
        logger.info(f"[JAM CLEARED] {jam_id} ({reason})")

    def _cleanup_old_jams(self):
        """Remove jams that haven't been updated recently."""
        now = time.time()
        jams_to_remove = []
        
        for jam_id, jam in self.active_jams.items():
            if jam.active and (now - jam.last_updated > self.JAM_EXPIRY_S):
                jam.active = False
                jams_to_remove.append(jam_id)
                self._publish_jam_cleared(jam, "expired")
                logger.info(f"[JAM EXPIRED] {jam_id}")
        
        # Remove expired jams
        for jam_id in jams_to_remove:
            del self.active_jams[jam_id]

    def _cleanup_old_car_states(self):
        """Remove car states that haven't been updated recently."""
        now = time.time()
        cars_to_remove = []
        
        for car_id, car_state in self.cars.items():
            if now - car_state.timestamp > self.CAR_STATE_EXPIRY_S:
                cars_to_remove.append(car_id)
        
        for car_id in cars_to_remove:
            del self.cars[car_id]
            logger.debug(f"[CLEANUP] Removed stale car state: {car_id}")

    def _on_car_update(self, payload: str):
        """
        Process incoming car updates from MQTT.
        
        Updates car state and triggers jam detection.
        """
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
            logger.error(f"Error processing car update: {e}")
            return

        # Ignore updates without speed; heading can be reused from last state.
        if update.speed_kmh is None:
            return

        now = time.time()
        
        # Update or create car state. Preserve last known heading/speed limit when
        # position-processor cannot infer them for very small movements.
        if update.car_id in self.cars:
            state = self.cars[update.car_id]
            heading = update.heading_deg if update.heading_deg is not None else state.heading_deg
            speed_limit = (
                update.speed_limit_kmh
                if update.speed_limit_kmh is not None
                else state.speed_limit_kmh
            )

            if heading is None:
                return

            state.latitude = update.latitude
            state.longitude = update.longitude
            state.speed_kmh = update.speed_kmh
            state.heading_deg = heading
            state.speed_limit_kmh = speed_limit
            state.timestamp = now
        else:
            if update.heading_deg is None or update.speed_limit_kmh is None:
                return

            self.cars[update.car_id] = CarState(
                car_id=update.car_id,
                latitude=update.latitude,
                longitude=update.longitude,
                speed_kmh=update.speed_kmh,
                heading_deg=update.heading_deg,
                speed_limit_kmh=update.speed_limit_kmh,
                timestamp=now,
            )

        # Detect traffic jams
        self._detect_traffic_jams()
        
        # Check if this car should be notified about existing jams
        if update.car_id in self.cars:
            car_state = self.cars[update.car_id]
            for jam in self.active_jams.values():
                if jam.active and self._should_notify_car(car_state, jam):
                    dist = haversine_distance_m(
                        car_state.latitude, car_state.longitude,
                        jam.center_latitude, jam.center_longitude
                    )
                    
                    notification = {
                        "notification_type": "traffic_jam_alert",
                        "target_car_id": update.car_id,
                        "jam_id": jam.jam_id,
                        "jam": jam.to_dict(),
                        "distance_m": dist,
                        "timestamp": now,
                    }
                    
                    self.mqtt.publish(f"alerts/traffic_jam/{update.car_id}", json.dumps(notification))
        
        # Cleanup old data
        self._cleanup_old_jams()
        self._cleanup_old_car_states()

    def _cleanup_car(self, car_id: str):
        """Remove all state for a specific car (used for test cleanup)."""
        # Remove car state
        if car_id in self.cars:
            del self.cars[car_id]
            logger.info(f"[CLEANUP] Removed car state: {car_id}")
        
        # Remove car from any active jams
        affected_jams = []
        for jam in self.active_jams.values():
            if car_id in jam.car_ids:
                jam.car_ids.discard(car_id)
                jam.last_updated = time.time()
                affected_jams.append(jam.jam_id)
                logger.info(f"[CLEANUP] Removed {car_id} from jam {jam.jam_id}")

        for jam_id in affected_jams:
            self._remove_jam_if_dissolved(jam_id, "test_cleanup")

    def run(self):
        logger.info("Starting Traffic Jam Detector...")
        logger.info(
            "Parameters: min_cars=%d, speed_threshold=%.0f%% of limit, "
            "proximity=%dm, heading_tolerance=+/-%d degrees",
            self.MIN_CARS_FOR_JAM,
            self.SPEED_THRESHOLD_RATIO * 100,
            self.PROXIMITY_RADIUS_M,
            self.HEADING_TOLERANCE_DEG
        )
        self.mqtt.connect()
        self.mqtt.subscribe(self.config.car_updates_topic, self._on_car_update)
        self.mqtt.loop_forever()


def main():
    setup_logging("traffic-jam-detector")
    config = load_config()
    detector = TrafficJamDetector(config)
    detector.run()


if __name__ == "__main__":
    main()