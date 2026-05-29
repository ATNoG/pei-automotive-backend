#!/usr/bin/env python3
#
# meteo_comparison.py
#
# Compares meteorological data from OpenWeather API with Ditto meteo things
#
import sys
import os
import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List
from dataclasses import dataclass

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from common.ditto_rest_client import DittoRestClient
from common.models import Station
from common.config import load_config

try:
    import requests
except ImportError:
    print("Error: requests library not found. Install with: pip install requests")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class OpenWeatherData:
    """Data from OpenWeather API"""
    temperature: float  # °C
    humidity: int  # %
    pressure: float  # hPa
    wind_speed: float  # m/s (will be converted to km/h)
    wind_direction: float  # degrees
    description: str
    timestamp: datetime
    location_name: str
    latitude: float
    longitude: float
    
    @property
    def wind_speed_kmh(self) -> float:
        """Convert wind speed from m/s to km/h"""
        return self.wind_speed * 3.6


class OpenWeatherClient:
    """Client for OpenWeather API"""
    
    def __init__(self, api_key: str, historical_date: Optional[datetime] = None):
        self.api_key = api_key
        self.historical_date = historical_date
        if historical_date:
            self.base_url = "https://api.openweathermap.org/data/3.0/onecall/timemachine"
        else:
            self.base_url = "https://api.openweathermap.org/data/2.5/weather"
    
    def get_weather_by_coords(self, lat: float, lon: float) -> Optional[OpenWeatherData]:
        """
        Fetch weather data for specific coordinates
        
        Args:
            lat: Latitude
            lon: Longitude
            
        Returns:
            OpenWeatherData or None if request fails
        """
        if self.historical_date:
            # Use Time Machine API for historical data
            # Convert datetime to Unix timestamp
            dt = int(self.historical_date.timestamp())
            params = {
                'lat': lat,
                'lon': lon,
                'dt': dt,
                'appid': self.api_key,
                'units': 'metric'  # Use Celsius
            }
        else:
            # Use current weather API
            params = {
                'lat': lat,
                'lon': lon,
                'appid': self.api_key,
                'units': 'metric'  # Use Celsius
            }
        
        try:
            response = requests.get(self.base_url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if self.historical_date:
                    return self._parse_timemachine_response(data)
                else:
                    return self._parse_response(data)
            else:
                logger.error(f"OpenWeather API error: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"Error fetching OpenWeather data: {e}")
            return None
    
    def _parse_response(self, data: Dict) -> OpenWeatherData:
        """Parse OpenWeather current weather API response"""
        main = data.get('main', {})
        wind = data.get('wind', {})
        weather = data.get('weather', [{}])[0]
        
        return OpenWeatherData(
            temperature=main.get('temp', 0.0),
            humidity=main.get('humidity', 0),
            pressure=main.get('pressure', 0.0),
            wind_speed=wind.get('speed', 0.0),
            wind_direction=wind.get('deg', 0.0),
            description=weather.get('description', 'N/A'),
            timestamp=datetime.fromtimestamp(data.get('dt', 0)),
            location_name=data.get('name', 'Unknown'),
            latitude=data.get('coord', {}).get('lat', 0.0),
            longitude=data.get('coord', {}).get('lon', 0.0),
        )
    
    def _parse_timemachine_response(self, data: Dict) -> OpenWeatherData:
        """Parse OpenWeather Time Machine API response"""
        hourly_data = data.get('data', [{}])[0]  # Get first hour
        
        return OpenWeatherData(
            temperature=hourly_data.get('temp', 0.0),
            humidity=hourly_data.get('humidity', 0),
            pressure=hourly_data.get('pressure', 0.0),
            wind_speed=hourly_data.get('wind_speed', 0.0),
            wind_direction=hourly_data.get('wind_deg', 0.0),
            description=hourly_data.get('weather', [{}])[0].get('description', 'N/A'),
            timestamp=datetime.fromtimestamp(hourly_data.get('dt', 0)),
            location_name='Historical Data',
            latitude=data.get('lat', 0.0),
            longitude=data.get('lon', 0.0),
        )

# Aveiro coordinates (Aveiro University)
AVEIRO_LAT = 40.6353
AVEIRO_LON = -8.6596
AVEIRO_RADIUS_KM = 50  # 50km radius around Aveiro

class MeteoComparison:
    """Compare Weather API meteo data with OpenWeather data"""
    
    def __init__(self, config, openweather_api_key: str, historical_date: Optional[datetime] = None, region_filter: str = None):
        # Use Weather API credentials for meteo data
        self.weather_client = DittoRestClient(
            api_url=config.weather_api_url,
            username=config.weather_username,
            password=config.weather_password,
        )
        self.openweather = OpenWeatherClient(openweather_api_key, historical_date)
        self.region_filter = region_filter
        self.openweather_cache = {}  # Cache OpenWeather calls
    
    def _is_in_region(self, lat: float, lon: float) -> bool:
        """Check if coordinates are within the selected region"""
        if not self.region_filter:
            return True
        
        if self.region_filter.lower() == 'aveiro':
            # Calculate distance from Aveiro center
            from math import radians, sin, cos, sqrt, atan2
            
            # Haversine formula
            R = 6371  # Earth radius in km
            lat1, lon1 = radians(AVEIRO_LAT), radians(AVEIRO_LON)
            lat2, lon2 = radians(lat), radians(lon)
            
            dlat = lat2 - lat1
            dlon = lon2 - lon1
            
            a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
            c = 2 * atan2(sqrt(a), sqrt(1-a))
            distance = R * c
            
            return distance <= AVEIRO_RADIUS_KM
        
        return True
    
    def compare_all_stations(self) -> List[Dict]:
        """
        Compare all weather API meteo stations with OpenWeather data
        
        Returns:
            List of comparison results
        """
        logger.info("Fetching meteo stations from Weather API...")
        meteo_things = self.weather_client.get_all_meteo_things()
        
        if not meteo_things:
            logger.warning("No meteo things found in Ditto")
            return []
        
        logger.info(f"Found {len(meteo_things)} total meteo stations")
        
        # Filter stations by region
        filtered_stations = []
        for thing_data in meteo_things:
            try:
                station = Station.from_ditto_thing(thing_data)
                if self._is_in_region(station.location.latitude, station.location.longitude):
                    filtered_stations.append(station)
            except Exception as e:
                logger.error(f"Error parsing station: {e}")
        
        logger.info(f"Filtered to {len(filtered_stations)} stations in {self.region_filter or 'all regions'}")
        
        # Make OpenWeather API call
        if self.region_filter and self.region_filter.lower() == 'aveiro':
            logger.info("Fetching OpenWeather data for Aveiro (single call)...")
            openweather_data = self.openweather.get_weather_by_coords(AVEIRO_LAT, AVEIRO_LON)
            if not openweather_data:
                logger.error("Failed to fetch OpenWeather data")
                return []
            self.openweather_cache['aveiro'] = openweather_data
        
        comparisons = []
        for station in filtered_stations:
            try:
                comparison = self.compare_station(station)
                if comparison:
                    comparisons.append(comparison)
            except Exception as e:
                logger.error(f"Error comparing station {station.station_id}: {e}", exc_info=True)
        
        return comparisons
    
    def compare_station(self, station: Station) -> Optional[Dict]:
        """
        Compare a single station with OpenWeather data
        
        Args:
            station: Station object from Ditto
            
        Returns:
            Dictionary with comparison results
        """
        if not station.measurement:
            logger.warning(f"Station {station.station_id} has no measurement data")
            return None
        
        logger.info(f"Comparing station {station.station_id}: {station.location_name}")
        
        # Use cached OpenWeather data if available
        if 'aveiro' in self.openweather_cache:
            ow_data = self.openweather_cache['aveiro']
        else:
            # Fetch OpenWeather data for the same location
            ow_data = self.openweather.get_weather_by_coords(
                station.location.latitude,
                station.location.longitude
            )
            
            if not ow_data:
                logger.warning(f"Could not fetch OpenWeather data for station {station.station_id}")
                return None
        
        # Calculate differences
        temp_diff = station.measurement.temperature - ow_data.temperature
        humidity_diff = station.measurement.humidity - ow_data.humidity
        pressure_diff = station.measurement.pressure - ow_data.pressure
        wind_speed_diff = station.measurement.wind_intensity - ow_data.wind_speed_kmh
        
        comparison = {
            'station_id': station.station_id,
            'location_name': station.location_name,
            'coordinates': {
                'latitude': station.location.latitude,
                'longitude': station.location.longitude,
            },
            'ditto_data': {
                'temperature': station.measurement.temperature,
                'humidity': station.measurement.humidity,
                'pressure': station.measurement.pressure,
                'wind_intensity': station.measurement.wind_intensity,
                'wind_direction': int(station.measurement.wind_direction),
                'radiation': station.measurement.radiation,
                'accumulated_precipitation': station.measurement.accumulated_precipitation,
                'timestamp': station.measurement.time.isoformat(),
            },
            'openweather_data': {
                'temperature': ow_data.temperature,
                'humidity': ow_data.humidity,
                'pressure': ow_data.pressure,
                'wind_speed': ow_data.wind_speed_kmh,
                'wind_direction': ow_data.wind_direction,
                'description': ow_data.description,
                'timestamp': ow_data.timestamp.isoformat(),
            },
            'differences': {
                'temperature': temp_diff,
                'humidity': humidity_diff,
                'pressure': pressure_diff,
                'wind_speed': wind_speed_diff,
            },
            'comparison_timestamp': datetime.now().isoformat(),
        }
        
        return comparison
    
    def generate_report(self, comparisons: List[Dict]):
        """
        Generate a simple comparison report to console
        
        Args:
            comparisons: List of comparison results
        """
        if not comparisons:
            logger.warning("No comparisons to report")
            return
        
        # Print simple station comparisons
        self._print_simple_comparisons(comparisons)
    
    def _print_simple_comparisons(self, comparisons: List[Dict]):
        """Print simple station comparisons to console"""
        for comp in comparisons:
            ditto = comp['ditto_data']
            ow = comp['openweather_data']
            diff = comp['differences']
            
            print(f"{comp['location_name']}")
            print(f"  Station ID: {comp['station_id']}")
            print(f"  Location: ({comp['coordinates']['latitude']:.4f}, {comp['coordinates']['longitude']:.4f})")
            print()
            print(f"  Temperature:  Weather API: {ditto['temperature']:>6.1f}°C  |  OpenWeather: {ow['temperature']:>6.1f}°C  |  Diff: {diff['temperature']:>+6.1f}°C")
            print(f"  Humidity:     Weather API: {ditto['humidity']:>6}%    |  OpenWeather: {ow['humidity']:>6}%    |  Diff: {diff['humidity']:>+6}%")
            print(f"  Pressure:     Weather API: {ditto['pressure']:>6.1f}hPa |  OpenWeather: {ow['pressure']:>6.1f}hPa |  Diff: {diff['pressure']:>+6.1f}hPa")
            print(f"  Wind Speed:   Weather API: {ditto['wind_intensity']:>6.1f}km/h|  OpenWeather: {ow['wind_speed']:>6.1f}km/h|  Diff: {diff['wind_speed']:>+6.1f}km/h")
            print()


def main():
    parser = argparse.ArgumentParser(
        description='Compare Ditto meteo data with OpenWeather API'
    )
    parser.add_argument(
        '--api-key',
        help='OpenWeather API key (or set OPENWEATHER_API_KEY env variable)',
        default=os.getenv('OPENWEATHER_API_KEY')
    )
    parser.add_argument(
        '--date',
        '-d',
        help='Historical date for OpenWeather data (format: YYYY-MM-DD or "YYYY-MM-DD HH:MM")',
        default='2026-02-17'
    )
    parser.add_argument(
        '--hour',
        '-H',
        type=int,
        help='Hour of the day for historical data (0-23)',
        default=None
    )
    
    args = parser.parse_args()
    
    logging.getLogger().setLevel(logging.WARNING)
    
    if not args.api_key:
        print("Error: OpenWeather API key is required. Use --api-key or set OPENWEATHER_API_KEY env variable")
        sys.exit(1)
    
    # Load configuration
    try:
        config = load_config()
    except Exception as e:
        print(f"Failed to load configuration: {e}")
        sys.exit(1)
    
    # Parse the historical date
    historical_date = None
    if args.date:
        try:
            # Try parsing with time first (YYYY-MM-DD HH:MM)
            try:
                historical_date = datetime.strptime(args.date, '%Y-%m-%d %H:%M')
            except ValueError:
                # Fall back to date only (YYYY-MM-DD)
                historical_date = datetime.strptime(args.date, '%Y-%m-%d').replace(hour=16)
            
            # Override hour if --hour parameter is provided
            if args.hour is not None:
                if 0 <= args.hour <= 23:
                    historical_date = historical_date.replace(hour=args.hour)
                else:
                    print(f"Invalid hour: {args.hour}. Must be between 0 and 23.")
                    sys.exit(1)
        except ValueError:
            print(f"Invalid date format: {args.date}. Use YYYY-MM-DD or 'YYYY-MM-DD HH:MM' format.")
            sys.exit(1)
    
    # Run comparison for Aveiro only
    comparison = MeteoComparison(config, args.api_key, historical_date, 'aveiro')
    all_comparisons = comparison.compare_all_stations()
    
    # Filter for only Aveiro University station (1210702)
    aveiro_uni_comparisons = [c for c in all_comparisons if c['station_id'] == 1210702]
    
    if aveiro_uni_comparisons:
        comparison.generate_report(aveiro_uni_comparisons)
    else:
        print("Aveiro (Universidade) station not found or has no data")
        sys.exit(1)
 
if __name__ == "__main__":
    main()