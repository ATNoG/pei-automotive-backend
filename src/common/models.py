# treated models from ditto (raw)
from dataclasses import dataclass, field
from typing import Dict, Optional
from enum import IntEnum
from datetime import datetime
import time
import json


class WindDirection(IntEnum):
    UNKNOWN = 0
    NORTH = 1
    NORTH_EAST = 2
    EAST = 3
    SOUTH_EAST = 4
    SOUTH = 5
    SOUTH_WEST = 6
    WEST = 7
    NORTH_WEST = 8


@dataclass
class Point:
    longitude: float
    latitude: float

    def to_dict(self) -> Dict:
        return {
            "longitude": self.longitude,
            "latitude": self.latitude,
        }


@dataclass
class Measurement:
    wind_intensity: float  # km/h
    temperature: float  # °C
    radiation: float  # W/m²
    wind_direction: WindDirection
    accumulated_precipitation: float  # mm
    pressure: float  # hPa
    humidity: int  # %
    time: datetime

    def to_dict(self) -> Dict:
        return {
            "wind_intensity": self.wind_intensity,
            "temperature": self.temperature,
            "radiation": self.radiation,
            "wind_direction": int(self.wind_direction),
            "accumulated_precipitation": self.accumulated_precipitation,
            "pressure": self.pressure,
            "humidity": self.humidity,
            "time": self.time.isoformat(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict) -> "Measurement":
        # Parse datetime if it's a string
        time_value = data.get("time")
        if isinstance(time_value, str):
            time_value = datetime.fromisoformat(time_value.replace('Z', '+00:00'))
        
        return cls(
            wind_intensity=float(data.get("wind_intensity", 0.0)),
            temperature=float(data.get("temperature", 0.0)),
            radiation=float(data.get("radiation", 0.0)),
            wind_direction=WindDirection(int(data.get("wind_direction", 0))),
            accumulated_precipitation=float(data.get("accumulated_precipitation", 0.0)),
            pressure=float(data.get("pressure", 0.0)),
            humidity=int(data.get("humidity", 0)),
            time=time_value,
        )


@dataclass
class Station:
    station_id: int
    location: Point
    location_name: str
    measurement: Optional[Measurement] = None

    def to_dict(self) -> Dict:
        return {
            "station_id": self.station_id,
            "location": self.location.to_dict(),
            "location_name": self.location_name,
            "measurement": self.measurement.to_dict() if self.measurement else None,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict) -> "Station":
        """Parse a dictionary into a Station object"""
        location_data = data.get("location", {})
        location = Point(
            longitude=location_data.get("longitude"),
            latitude=location_data.get("latitude"),
        )
        
        measurement = None
        measurement_data = data.get("measurement")
        if measurement_data:
            measurement = Measurement.from_dict(measurement_data)
        
        return cls(
            station_id=data.get("station_id"),
            location=location,
            location_name=data.get("location_name", ""),
            measurement=measurement,
        )

    @classmethod
    def from_ditto_thing(cls, thing_data: Dict) -> "Station":
        """Parse a Ditto thing JSON into a Station object"""
        attributes = thing_data.get("attributes", {})
        station_id = attributes.get("id")
        
        location_data = attributes.get("location", {})
        location = Point(
            longitude=location_data.get("longitude"),
            latitude=location_data.get("latitude"),
        )
        
        location_name = attributes.get("location_name", "")
        
        # Parse measurement from features.meteorology.properties
        measurement = None
        features = thing_data.get("features", {})
        meteorology = features.get("meteorology", {})
        properties = meteorology.get("properties", {})
        
        if properties:
            measurement = Measurement.from_dict(properties)
        
        return cls(
            station_id=station_id,
            location=location,
            location_name=location_name,
            measurement=measurement,
        )


class AlertPriority(IntEnum):
    """Alert priority levels for event aggregation.
    
    Higher values = higher priority. Frontend will only display/play
    alerts with priority > current_alert_priority.
    """
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class AlertMetadata:
    """Common metadata for all backend alerts.
    
    Used to implement priority-based event aggregation in the frontend.
    The frontend tracks current_alert_priority and current_alert_priority_audio,
    and will only display/play alerts with higher priority that are still valid.
    """
    priority: int = AlertPriority.MEDIUM  # AlertPriority enum value
    expiration_s: int = 10  # seconds until this alert expires (time-to-live)
    timestamp: float = field(default_factory=time.time)
    
    def is_valid(self) -> bool:
        """Check if alert is still within its expiration window."""
        return time.time() < self.timestamp + self.expiration_s
    
    def to_dict(self) -> Dict:
        return {
            "priority": int(self.priority),
            "expiration_s": self.expiration_s,
            "timestamp": self.timestamp,
        }


@dataclass
class CarUpdate:
    # treated car
    car_id: str
    latitude: float
    longitude: float
    speed_kmh: Optional[float] = None
    heading_deg: Optional[float] = None
    speed_limit_kmh: Optional[float] = None
    emergency: bool = False
    tile_quadkey: Optional[int] = None
    tile_zoom: Optional[int] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return {
            "car_id": self.car_id,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "speed_kmh": self.speed_kmh,
            "heading_deg": self.heading_deg,
            "speed_limit_kmh": self.speed_limit_kmh,
            "emergency": self.emergency,
            "tile_quadkey": self.tile_quadkey,
            "tile_zoom": self.tile_zoom,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict) -> "CarUpdate":
        return cls(
            car_id=data["car_id"],
            latitude=data["latitude"],
            longitude=data["longitude"],
            speed_kmh=data.get("speed_kmh"),
            heading_deg=data.get("heading_deg"),
            speed_limit_kmh=data.get("speed_limit_kmh"),
            emergency=data.get("emergency", False),
            tile_quadkey=data.get("tile_quadkey"),
            tile_zoom=data.get("tile_zoom"),
            timestamp=data.get("timestamp", time.time()),
        )