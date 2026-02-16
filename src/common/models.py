# treated models from ditto (raw)
from dataclasses import dataclass, field
from typing import Dict, Optional
import time
import json


@dataclass
class CarUpdate:
    # treated car
    car_id: str
    latitude: float
    longitude: float
    speed_kmh: Optional[float] = None
    heading_deg: Optional[float] = None
    speed_limit_kmh: Optional[float] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        return {
            "car_id": self.car_id,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "speed_kmh": self.speed_kmh,
            "heading_deg": self.heading_deg,
            "speed_limit_kmh": self.speed_limit_kmh,
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
            timestamp=data.get("timestamp", time.time()),
        )