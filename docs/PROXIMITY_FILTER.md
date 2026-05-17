# Proximity Filter

## What it is

A microservice (`src/services/proximity_filter`) that serves as the **Ditto
gateway** for the backend. It replaces the position_processor as the entry
point for car GPS events from Ditto, enriching each update with a
`tile_quadkey` before forwarding it over MQTT for the position_processor
to consume.

## Architecture

```
Ditto WS
    │
    ▼
proximity_filter          (computes tile_quadkey at PROXIMITY_ZOOM)
    │  {car_id, lat, lon, emergency, tile_quadkey, tile_zoom}
    ▼
cars/raw_updates/<car_id>
    │
    ▼
position_processor        (computes speed, heading, speed_limit; passes tile fields through)
    │  CarUpdate {... tile_quadkey, tile_zoom}
    ▼
cars/updates/<car_id>
    │
    ├── overtaking_detector    (cars_by_tile — only compares cars in same tile)
    ├── traffic_jam_detector   (cars_by_tile — cluster search stays tile-local)
    ├── accident_detector      (cars_by_tile — notifications stay tile-local)
    ├── emergency_vehicle_detector
    ├── speed_detector
    └── highway_entry_detector
```

- **`proximity_filter`** connects directly to the Ditto WebSocket. For every
  GPS event it receives, it computes a QuadTree quadkey at `PROXIMITY_ZOOM`
  (default 15 — ≈1.2 km tile near 41° N) and publishes
  `{car_id, lat, lon, emergency, tile_quadkey, tile_zoom}` to
  `cars/raw_updates/<car_id>`. It has no MQTT subscriptions.
- **`position_processor`** is now a pure MQTT service. It subscribes to
  `cars/raw_updates/+`, computes speed, heading, and speed limit from
  consecutive positions, and publishes an enriched `CarUpdate` (including
  the tile fields passed through from the proximity_filter) to
  `cars/updates/<car_id>`.
- **Detectors** subscribe to `cars/updates/+` as before and bucket their
  internal car state by `tile_quadkey` to avoid cross-tile comparisons.

The QuadTree encoding follows Igor Coelho's specification (recursive 2-bit
quadrants packed into a signed 64-bit integer; max zoom 31). The implementation
lives in `src/common/geotile.py` and is treated as canonical.

## Files changed

| File | Change |
| --- | --- |
| `src/common/geotile.py` | **new** — `get_quadkey(lat, lng, zoom)` and `get_tile_bounds(lat, lng, tile_zoom, max_zoom=31)`. |
| `src/common/config.py` | Added `raw_car_updates_topic` (env `MQTT_RAW_CAR_UPDATES_TOPIC`, default `cars/raw_updates`). |
| `src/common/models.py` | Added `tile_quadkey: Optional[int]` and `tile_zoom: Optional[int]` to `CarUpdate`. |
| `src/services/proximity_filter/service.py` | **new** — connects to Ditto WS, computes tile_quadkey, publishes to `cars/raw_updates/<car_id>`. |
| `src/services/proximity_filter/Dockerfile` | **new** |
| `src/services/position_processor/service.py` | Removed Ditto WS connection. Now a pure MQTT service: subscribes to `cars/raw_updates/+`, passes tile fields through, publishes to `cars/updates/<car_id>`. |
| `src/services/overtaking_detector/service.py` | Tile bucketing — `cars_by_tile` dict, only compares cars in same tile. |
| `src/services/traffic_jam_detector/service.py` | Tile bucketing — cluster search scoped to reference car's tile. |
| `src/services/accident_detector/service.py` | Tile bucketing — notification scans and existence checks scoped to same tile. |
| `src/services/emergency_vehicle_detector/service.py` | Tile bucketing. |
| `docker-compose.yml` | `proximity_filter` starts before `position_processor`. Detectors depend only on `position_processor`. |
| `tests/test_proximity_filter.py` | Tests for geotile primitives, `_on_gps_update` enrichment, and end-to-end tile isolation. |
| `tests/test_detector_tile_bucketing.py` | Unit tests for per-detector tile bucketing. |
| `tests/conftest.py` | Cleanup now publishes sentinel to both `cars/raw_updates/<car_id>` (position_processor) and `cars/updates/<car_id>` (detectors). |

## Configuration

| Env var | Default | Description |
| --- | --- | --- |
| `PROXIMITY_ZOOM` | `15` | QuadTree zoom level for tile_quadkey (~1.2 km tiles at zoom 15 near 41° N). |
| `MQTT_RAW_CAR_UPDATES_TOPIC` | `cars/raw_updates` | Internal bridge topic between proximity_filter and position_processor. |

## How to run

```
docker compose up --build
```

`proximity_filter` starts first (it owns the Ditto WS connection), then
`position_processor`, then all detectors.

Run the tile-isolation integration test with the full stack up:

```
pytest --fixed-ids tests/test_proximity_filter.py::test_proximity_filter_end_to_end
```
