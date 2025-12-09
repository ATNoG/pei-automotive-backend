import math

def haversine_distance_m(lat1, lon1, lat2, lon2) -> float:
    # calculates the distance between two points in a straight-line
    # its better like this because it takes the earth's curvature into account
    R = 6371000.0  # earth radius (m)
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*(math.sin(dlambda/2)**2)
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    # calculates heading(rumo) from pos1 to pos2 in degrees
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)

    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)

    brng = math.degrees(math.atan2(x, y))
    # normalize to [0, 360]
    return (brng + 360.0) % 360.0
