#
# Accident Detector
# Detects potential accidents based on sudden stops (drastic speed reduction)
# Notifies only vehicles that have the accident AHEAD of them (not yet passed)
#
from __future__ import annotations
import json
import logging
import sys
import time
from pathlib import Path
from typing import Dict, Optional
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
    """Tracks a car's recent state for accident detection."""
    car_id: str
    latitude: float
    longitude: float
    speed_kmh: Optional[float] = None
    heading_deg: Optional[float] = None
    timestamp: float = field(default_factory=time.time)
    previous_speed_kmh: Optional[float] = None


@dataclass
class Accident:
    """
    Represents a detected accident event.
    
    Aligned with domain model (AccidentEvent table):
    - eventId: int (PK, auto-generated)
    - locationId: FK to Location (contains lat/lon)
    - numberVehicles: int NN
    
    For now, we embed lat/lon directly until DB integration.
    """
    event_id: str
    latitude: float
    longitude: float
    source_vehicle_id: str
    detected_at: float
    active: bool = True
    # TODO: DB integration fields
    # location_id: Optional[int] = None
    # number_vehicles: Optional[int] = None

    def to_dict(self) -> Dict:
        return {
            "event_id": self.event_id,
            "type": "accident",
            "latitude": self.latitude,
            "longitude": self.longitude,
            "source_vehicle_id": self.source_vehicle_id,
            "detected_at": self.detected_at,
            "active": self.active,
        }


class AccidentDetector:
    # Detection: percentage-based (works for any speed limit)
    SPEED_DROP_PERCENT = 0.70  # 70% reduction = sudden stop
    MIN_SPEED_BEFORE_STOP = 30  # km/h - must be moving at reasonable speed
    STOPPED_THRESHOLD_KMH = 5  # below this = stopped

    # Notification
    NOTIFICATION_RADIUS_M = 500  # notify cars within 500m
    PROXIMITY_RADIUS_M = 50  # same-location threshold
    ACCIDENT_COOLDOWN_S = 60  # prevent duplicates
    ACCIDENT_EXPIRY_S = 300  # 5 min expiry

    def __init__(self, config):
        self.config = config
        self.mqtt = MQTTClient(
            host=config.broker_host,
            port=config.broker_port,
            username=config.broker_user,
            password=config.broker_password,
            client_id="accident-detector",
        )

        self.cars: Dict[str, CarState] = {}
        self.active_accidents: Dict[str, Accident] = {}
        self.alert_topic = "alerts/accident"
        self.event_counter = 0

    def _generate_event_id(self) -> str:
        self.event_counter += 1
        return f"ACC-{int(time.time())}-{self.event_counter}"

    def _is_accident_ahead(self, car: CarState, accident: Accident) -> bool:
        """
        Determine if the accident is AHEAD of the car.
        
        Uses the car's heading to check if the accident location
        is in front of it. Works for any road geometry (curves included).
        
        Returns True if accident is ahead, False if car already passed.
        """
        if car.heading_deg is None:
            return False

        # Bearing from car to accident
        bearing_to_accident = bearing_deg(
            car.latitude, car.longitude,
            accident.latitude, accident.longitude
        )

        # Angular difference
        heading_diff = abs(car.heading_deg - bearing_to_accident)
        heading_diff = min(heading_diff, 360 - heading_diff)

        # < 90° means accident is in front of the car
        # > 90° means car has already passed the accident
        return heading_diff < 90

    def _should_notify_car(self, car: CarState, accident: Accident) -> bool:
        """
        Check if a car should receive an accident notification.
        
        Conditions:
        1. Not the accident vehicle
        2. Within notification radius
        3. Moving (not stopped)
        4. Accident is AHEAD (not already passed)
        """
        if car.car_id == accident.source_vehicle_id:
            return False

        if car.speed_kmh is None or car.speed_kmh < self.STOPPED_THRESHOLD_KMH:
            return False

        dist = haversine_distance_m(
            car.latitude, car.longitude,
            accident.latitude, accident.longitude
        )
        if dist > self.NOTIFICATION_RADIUS_M:
            return False

        return self._is_accident_ahead(car, accident)

    def _check_accident_cooldown(self, lat: float, lon: float) -> bool:
        """Check if recent accident exists near this location."""
        now = time.time()

        for accident in self.active_accidents.values():
            if not accident.active:
                continue

            dist = haversine_distance_m(lat, lon, accident.latitude, accident.longitude)
            time_since = now - accident.detected_at

            if dist <= self.PROXIMITY_RADIUS_M and time_since < self.ACCIDENT_COOLDOWN_S:
                return True

        return False

    def _notify_nearby_vehicles(self, accident: Accident) -> int:
        """Notify vehicles with accident ahead. Returns count."""
        notified = 0

        for car_id, car_state in self.cars.items():
            if self._should_notify_car(car_state, accident):
                dist = haversine_distance_m(
                    car_state.latitude, car_state.longitude,
                    accident.latitude, accident.longitude
                )

                notification = {
                    "notification_type": "accident_alert",
                    "target_car_id": car_id,
                    "event_id": accident.event_id,
                    "accident": accident.to_dict(),
                    "distance_m": dist,
                    "timestamp": time.time(),
                }

                self.mqtt.publish(f"alerts/accident/{car_id}", json.dumps(notification))
                self.mqtt.publish(self.alert_topic, json.dumps(notification))

                logger.info(f"[ACCIDENT] Notified {car_id} - ahead at {dist:.0f}m")
                notified += 1

        return notified

    def _cleanup_expired_accidents(self):
        """Mark expired accidents as inactive."""
        now = time.time()

        for event_id, accident in self.active_accidents.items():
            if accident.active and (now - accident.detected_at > self.ACCIDENT_EXPIRY_S):
                accident.active = False
                logger.info(f"[ACCIDENT] Expired: {event_id}")

    def _detect_sudden_stop(self, update: CarUpdate, state: CarState) -> bool:
        """
        Detect sudden stop using percentage-based speed reduction.
        Works for any road/speed limit.
        """
        if state.previous_speed_kmh is None or update.speed_kmh is None:
            return False

        prev = state.previous_speed_kmh

        if prev < self.MIN_SPEED_BEFORE_STOP:
            return False

        if update.speed_kmh > self.STOPPED_THRESHOLD_KMH:
            return False

        reduction = (prev - update.speed_kmh) / prev
        return reduction >= self.SPEED_DROP_PERCENT

    def _on_car_update(self, payload: str):
        try:
            data = json.loads(payload)
            update = CarUpdate.from_dict(data)
        except Exception as e:
            logger.error(f"Error processing car update: {e}")
            return

        now = time.time()
        existing = self.cars.get(update.car_id)

        if existing is None:
            self.cars[update.car_id] = CarState(
                car_id=update.car_id,
                latitude=update.latitude,
                longitude=update.longitude,
                speed_kmh=update.speed_kmh,
                heading_deg=update.heading_deg,
                timestamp=now,
            )
            return

        state = existing
        state.previous_speed_kmh = state.speed_kmh

        # Detect sudden stop
        if self._detect_sudden_stop(update, state):
            logger.warning(
                f"[SUDDEN STOP] {update.car_id}: "
                f"{state.previous_speed_kmh:.1f} -> {update.speed_kmh:.1f} km/h"
            )

            if not self._check_accident_cooldown(update.latitude, update.longitude):
                accident = Accident(
                    event_id=self._generate_event_id(),
                    latitude=update.latitude,
                    longitude=update.longitude,
                    source_vehicle_id=update.car_id,
                    detected_at=now,
                )

                self.active_accidents[accident.event_id] = accident

                logger.warning(
                    f"[ACCIDENT DETECTED] {accident.event_id} at "
                    f"({update.latitude:.6f}, {update.longitude:.6f})"
                )

                # Initial notification attempt (cars already in range)
                self._notify_nearby_vehicles(accident)

        # Update state
        state.latitude = update.latitude
        state.longitude = update.longitude
        state.speed_kmh = update.speed_kmh
        state.heading_deg = update.heading_deg
        state.timestamp = now

        # Notify about existing accidents if this car qualifies
        for accident in self.active_accidents.values():
            if accident.active and self._should_notify_car(state, accident):
                dist = haversine_distance_m(
                    state.latitude, state.longitude,
                    accident.latitude, accident.longitude
                )

                notification = {
                    "notification_type": "accident_alert",
                    "target_car_id": update.car_id,
                    "event_id": accident.event_id,
                    "accident": accident.to_dict(),
                    "distance_m": dist,
                    "timestamp": now,
                }

                self.mqtt.publish(f"alerts/accident/{update.car_id}", json.dumps(notification))

        self._cleanup_expired_accidents()

    def run(self):
        logger.info("Starting Accident Detector...")
        self.mqtt.connect()
        self.mqtt.subscribe(self.config.car_updates_topic, self._on_car_update)
        self.mqtt.loop_forever()


def main():
    setup_logging("accident-detector")
    config = load_config()
    detector = AccidentDetector(config)
    detector.run()


if __name__ == "__main__":
    main()
