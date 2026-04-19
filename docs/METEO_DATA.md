# WindDirection

An enumeration representing the 8 cardinal and intercardinal compass
directions, plus an unknown state.

| Direction  | Value   | Comments                          |
| ---------- | ------- | --------------------------------- |
| NORTH      | 1       |                                   |
| NORTH_EAST | 2       |                                   |
| EAST       | 3       |                                   |
| SOUTH_EAST | 4       |                                   |
| SOUTH      | 5       |                                   |
| SOUTH_WEST | 6       |                                   |
| WEST       | 7       |                                   |
| NORTH_WEST | 8       |                                   |
| UNKNOWN    | 0       | Direction could not be determined |

# Point 

Represents a geographic coordinate, in the WGS84 coordinate system

| Field     | Type  |
| --------- | ----- |
| longitude | float |
| latitude  | float |

# Measurement

A single meteorological snapshot captured by a weather station at a specific
point in time.

| Field                     | Type          | Unit     |
| ------------------------- | ------------- | -------- |
| wind_intensity            | float         | km/h     |
| temperature               | float         | °C       |
| radiation                 | float         | W/m²     |
| wind_direction            | WindDirection | —        |
| accumulated_precipitation | float         | mm       |
| pressure                  | float         | hPa      |
| humidity                  | int           | %        |
| time                      | datetime      | ISO 8601 |

# Station

Represents a physical weather station deployed in the field.

| Field         | Type  | Comment                                                 |
| ------------- | ----- | ------------------------------------------------------- |
| id            | int   |                                                         |
| location      | Point |                                                         |
| location_name | str   | A human-readable name describing the station's location |

# Example
```json
{
  "thingId": "meteo:11217225",
  "policyId": "meteo:default",
  "attributes": {
    "id": 11217225,
    "location": {
      "longitude": -25.772,
      "latitude": 37.8679
    },
    "location_name": "São Miguel / Sete Cidades (DRAAC)"
  },
  "features": {
    "meteorology": {
      "properties": {
        "wind_intensity": 22.3,
        "temperature": 15.6,
        "radiation": 1806.7,
        "wind_direction": 6,
        "accumulated_precipitation": 0,
        "pressure": -99,
        "humidity": 90,
        "time": "2026-02-19T15:00:00"
      }
    }
  }
}
```