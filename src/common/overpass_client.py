import requests
from typing import Dict, Tuple
import time

OVERPASS_API_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_API_URL = "https://nominatim.openstreetmap.org/reverse"

# Global default speed limit (km/h) used as the ultimate fallback
# when neither Overpass nor Nominatim can determine the real limit.
DEFAULT_SPEED_LIMIT_KMH = 50

# Speed limit heuristics based on road type (in km/h)
ROAD_TYPE_SPEED_LIMITS = {
    "motorway": "120",
    "trunk": "100",
    "primary": "90",
    "secondary": "80",
    "tertiary": "60",
    "residential": str(DEFAULT_SPEED_LIMIT_KMH),
    "living_street": "20",
    "service": "30",
    "unclassified": str(DEFAULT_SPEED_LIMIT_KMH),
}

# Cache for speed limits (location -> speed limit)
# Key: (rounded_lat, rounded_lon) to cache by ~100m grid
_speed_limit_cache: Dict[Tuple[float, float], str] = {}
_last_api_call_time = 0
_min_api_call_interval = 1.0  # Minimum 1 second between API calls


def get_speed_limit_from_nominatim(lat: float, lon: float) -> str:
    """
    Fallback: Use Nominatim to get road type and estimate speed limit.
    Returns DEFAULT_SPEED_LIMIT_KMH if the API fails or no data is found.
    """
    try:
        response = requests.get(
            NOMINATIM_API_URL,
            params={
                "lat": lat,
                "lon": lon,
                "format": "json",
                "addressdetails": 1,
                "extratags": 1,
            },
            headers={"User-Agent": "PEI-Automotive-Backend/1.0"},
            timeout=3,
        )
        response.raise_for_status()
        data = response.json()

        # Check if maxspeed is in extratags
        extratags = data.get("extratags", {})
        if "maxspeed" in extratags:
            return extratags["maxspeed"]

        # Use road type heuristics
        road_type = data.get("type", "")
        if road_type in ROAD_TYPE_SPEED_LIMITS:
            return ROAD_TYPE_SPEED_LIMITS[road_type]

        # Check address details for highway classification
        address = data.get("address", {})
        if "road" in address:
            return str(DEFAULT_SPEED_LIMIT_KMH)

        return str(DEFAULT_SPEED_LIMIT_KMH)
    except Exception as e:
        print(f"Error consulting Nominatim API: {e}")
        return str(DEFAULT_SPEED_LIMIT_KMH)


def get_speed_limit(lat: float, lon: float) -> str:
    """
    Consult the Overpass API to obtain the speed limit (maxspeed) near the coordinates.
    Uses caching and rate limiting to avoid overwhelming the APIs.
    Falls back to Nominatim, then to DEFAULT_SPEED_LIMIT_KMH.
    """
    global _last_api_call_time

    # Round coordinates to ~100m grid for caching (3 decimal places ≈ 111m)
    cache_key = (round(lat, 3), round(lon, 3))

    # Check cache first
    if cache_key in _speed_limit_cache:
        return _speed_limit_cache[cache_key]

    # Rate limiting - wait if we're calling APIs too frequently
    current_time = time.time()
    time_since_last_call = current_time - _last_api_call_time
    if time_since_last_call < _min_api_call_interval:
        time.sleep(_min_api_call_interval - time_since_last_call)

    _last_api_call_time = time.time()

    query = f"""
    [out:json][timeout:25];
    (
      way(around:5,{lat},{lon})["maxspeed"];
      node(around:5,{lat},{lon})["maxspeed"];
    );
    out body;
    >;
    out skel qt;
    """
    try:
        response = requests.get(
            OVERPASS_API_URL,
            params={"data": query},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        for element in data.get("elements", []):
            tags = element.get("tags", {})
            speed_limit = None
            if "maxspeed" in tags:
                speed_limit = tags["maxspeed"]
            elif "maxspeed:forward" in tags:
                speed_limit = tags["maxspeed:forward"]
            elif "maxspeed:backward" in tags:
                speed_limit = tags["maxspeed:backward"]

            if speed_limit:
                # Cache the result
                _speed_limit_cache[cache_key] = speed_limit
                return speed_limit

        # If no speed limit found in Overpass, try Nominatim as fallback
        print("No speed limit found in Overpass, trying Nominatim fallback...")
        result = get_speed_limit_from_nominatim(lat, lon)
        _speed_limit_cache[cache_key] = result
        return result

    except Exception as e:
        print(f"Error consulting Overpass API: {e}")
        # Try Nominatim as fallback
        print("Trying Nominatim as fallback...")
        result = get_speed_limit_from_nominatim(lat, lon)
        # Cache even failures to avoid repeated API calls for same location
        _speed_limit_cache[cache_key] = result
        return result