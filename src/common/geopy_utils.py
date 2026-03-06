from geopy.distance import geodesic
from geopy.point import Point
import json
from typing import Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from common.models import Station


def find_nearest_station(car_lat: float, car_lon: float, stations: Dict[int, 'Station']) -> Optional['Station']:
    """
    Find the nearest weather station to a car's position.
    
    Args:
        car_lat: Car's latitude
        car_lon: Car's longitude
        stations: Dictionary of station_id -> Station objects
    
    Returns:
        The nearest Station object, or None if no stations available
    """
    if not stations:
        return None
    
    car_point = (car_lat, car_lon)
    nearest_station = None
    min_distance = float('inf')
    
    for station in stations.values():
        if station.location:
            station_point = (station.location.latitude, station.location.longitude)
            distance = geodesic(car_point, station_point).meters
            
            if distance < min_distance:
                min_distance = distance
                nearest_station = station
    
    return nearest_station


def generate_steps(start_lat, start_lon, end_lat, end_lon, step_m=5):
    start = Point(start_lat, start_lon)
    end = Point(end_lat, end_lon)
    total_dist = geodesic(start, end).meters
    steps = int(total_dist // step_m)
    points = []
    for i in range(steps + 1):
        fraction = i / steps
        lat = start_lat + (end_lat - start_lat) * fraction
        lon = start_lon + (end_lon - start_lon) * fraction
        points.append([lat, lon])
    return points

# Generate coordinates
coords = generate_steps(40.627988, -8.731973, 40.627991, -8.73242)

# Print in the requested format
print(json.dumps(coords, indent=2))