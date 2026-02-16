from geopy.distance import geodesic
from geopy.point import Point
import json

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