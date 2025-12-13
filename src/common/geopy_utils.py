from geopy.distance import geodesic
from geopy.point import Point

def generate_steps(start_lat, start_lon, end_lat, end_lon, step_m=10):
    start = Point(start_lat, start_lon)
    end = Point(end_lat, end_lon)
    total_dist = geodesic(start, end).meters
    steps = int(total_dist // step_m)
    points = []
    for i in range(steps + 1):
        fraction = i / steps
        lat = start_lat + (end_lat - start_lat) * fraction
        lon = start_lon + (end_lon - start_lon) * fraction
        points.append((lat, lon))
    return points

print(generate_steps(40.62834765, -8.73343953, 40.63124561, -8.74152154))