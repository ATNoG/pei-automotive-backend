import requests

OVERPASS_API_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_API_URL = "https://nominatim.openstreetmap.org/reverse"

# Speed limit heuristics based on road type (in km/h)
ROAD_TYPE_SPEED_LIMITS = {
    "motorway": "120",
    "trunk": "100",
    "primary": "90",
    "secondary": "80",
    "tertiary": "60",
    "residential": "50",
    "living_street": "20",
    "service": "30",
    "unclassified": "50"
}

def get_speed_limit_from_nominatim(lat: float, lon: float) -> str:
    """
    Fallback: Use Nominatim to get road type and estimate speed limit.
    """
    try:
        response = requests.get(
            NOMINATIM_API_URL,
            params={
                "lat": lat,
                "lon": lon,
                "format": "json",
                "addressdetails": 1,
                "extratags": 1
            },
            headers={"User-Agent": "PEI-Automotive-Backend/1.0"},
            timeout=5
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
            # Default for roads
            return "50"
        
        return "--"
    except Exception as e:
        print(f"Error consulting Nominatim API: {e}")
        return "--"

def get_speed_limit(lat: float, lon: float) -> str:
    """
    Consult the Overpass API to obtain the speed limit (maxspeed) near the coordinates.
    """
    query = f'''
    [out:json][timeout:25];
    (
      way(around:5,{lat},{lon})["maxspeed"];
      node(around:5,{lat},{lon})["maxspeed"];
    );
    out body;
    >;
    out skel qt;
    '''
    try:
        response = requests.get(
            OVERPASS_API_URL,
            params={"data": query},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        for element in data.get("elements", []):
            tags = element.get("tags", {})
            if "maxspeed" in tags:
                return tags["maxspeed"]
            if "maxspeed:forward" in tags:
                return tags["maxspeed:forward"]
            if "maxspeed:backward" in tags:
                return tags["maxspeed:backward"]
        
        # If no speed limit found in Overpass, try Nominatim as fallback
        print("No speed limit found in Overpass, trying Nominatim fallback...")
        return get_speed_limit_from_nominatim(lat, lon)
    
    except Exception as e:
        print(f"Error consulting Overpass API: {e}")
        # Try Nominatim as fallback
        print("Trying Nominatim as fallback...")
        return get_speed_limit_from_nominatim(lat, lon)
