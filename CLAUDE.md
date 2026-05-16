# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Backend system for an automotive telemetry platform. Microservices process vehicle GPS data from Eclipse Ditto digital twins, detect driving events, and publish alerts to an MQTT broker for the frontend to consume. The infrastructure runs on Eclipse cloud2edge (Ditto + Hono) deployed via Kubernetes (k3s + helm), with microservices running in Docker.

## Environment Setup

Copy and populate `.env` before running anything. Required variables:

```
DITTO_API_URL, DITTO_WS_URL, DITTO_USER, DITTO_PASS
HONO_API_URL, HONO_USER, HONO_PASS, HONO_TENANT, CERT
MQTT_BROKER_HOST, MQTT_BROKER_PORT
WEATHER_API_URL, WEATHER_USER, WEATHER_PASS
KEYCLOAK_ADMIN, KEYCLOAK_ADMIN_PASSWORD, KC_DB_USERNAME, KC_DB_PASSWORD, KEYCLOAK_REALM
APP_DB_USER, APP_DB_PASSWORD
```

The `make_env.sh` script auto-generates `.env` from a running k3s deployment.

## Commands

### Deploy cloud2edge (k3s + helm)
```bash
./deploy.sh        # full k3s + helm deployment
./stop.sh          # teardown
```

### Run services
```bash
docker compose up --build   # start all microservices
```

### Create a vehicle and send positions
```bash
python3 simulations/create_car.py <car_name>
python3 simulations/send_position.py <car_name> <lat> <lon>
```

### Run tests
```bash
pip install -r requirements.txt
pytest tests/test_speeding.py          # single test file
pytest tests/                          # all tests
pytest tests/ --fixed-ids              # use deterministic car IDs (useful for frontend testing)
bash scripts/run_tests.sh              # interactive: pick test + repeat count
```

### SUMO simulation bridge
```bash
# Run SUMO and push vehicle positions to Ditto
python scripts/bridge.py --cfg simulations/SUMO/osm.sumocfg

# Run a specific lane-merge scenario
python scripts/bridge.py \
    --cfg simulations/SUMO/scenarios/lanemerge/network/lanemerge.sumocfg \
    --route-files simulations/SUMO/scenarios/lanemerge/scenarios/scenario_07.rou.xml \
    --step-length 0.5 --end-time 120 --real-time --cleanup
```

### Evaluate a scenario pack
```bash
python scripts/eval.py --pack lanemerge
python scripts/eval.py --pack lanemerge --scenarios 7 10
python scripts/eval.py --pack lanemerge --gui --scenarios 1
python scripts/eval.py --pack lanemerge --output /tmp/eval.json
```

## Architecture

### Data flow

```
Vehicle GPS (Hono MQTT) → Eclipse Ditto (digital twin)
    → DittoWSClient (WebSocket) → PositionProcessor
        → enriched CarUpdate (speed, heading, speed limit) → MQTT cars/updates
            → detector services (subscribe to cars/updates)
                → alert payloads → MQTT alerts/<type>
                    → frontend (WebSocket/MQTT)
```

### `src/common/` — shared library

- **`config.py`** — loads all env vars into `AppConfig` dataclass via `load_config()`
- **`models.py`** — shared data models: `CarUpdate`, `Station`, `Measurement`, `AlertMetadata`, `AlertPriority`
- **`mqtt_client.py`** — thin paho-mqtt wrapper with subscribe/publish/loop
- **`ditto_client.py`** — WebSocket client that streams GPS updates from Ditto and calls `on_gps_update`
- **`ditto_rest_client.py`** — REST client for Ditto thing provisioning
- **`overpass_client.py`** — speed-limit resolver; uses `data/offline_roads/offline_roads.json` snapshot first, Overpass API as fallback
- **`geopy_utils.py`** / **`utils.py`** — haversine distance, bearing calculation

### `src/services/` — microservices (one Docker container each)

Each service has a `service.py` with a class following this pattern:
1. `__init__`: create `MQTTClient`, subscribe to `cars/updates`
2. `_on_car_update`: parse `CarUpdate`, apply detection logic, publish alert
3. `run`: connect MQTT, start loop

| Service | Detection logic | Alert topic |
|---|---|---|
| `position_processor` | Computes speed/heading from GPS deltas; resolves speed limit via Overpass | `cars/updates` |
| `speed_detector` | Speed > `speed_limit_kmh` | `alerts/speed` |
| `overtaking_detector` | Side-by-side vehicles in opposite lateral positions | `alerts/overtaking` |
| `lane_merge_detector` | Vehicle merging across lanes at intersection | `alerts/lane_merge` |
| `accident_detector` | Sudden deceleration pattern | `alerts/accident` |
| `traffic_jam_detector` | 5+ cars clustered ≤500m, speed <30% of limit, same heading ±30° | `alerts/traffic_jam/<car_id>` |
| `emergency_vehicle_detector` | Car with `emergency=true` flag nearby | `alerts/emergency` |
| `meteo_consumer` | Polls weather Ditto instance | `meteo/updates` |
| `station_assigner` | Assigns nearest meteo station to each car | `cars/station/<car_id>` |
| `database_api` | FastAPI service; stores user preferences in PostgreSQL, Keycloak auth | port 8082 |

### `database_api` structure

FastAPI app with asyncpg pool, Keycloak JWT middleware, routers for `/preferences` and `/users`.

### MQTT topics

| Topic | Publisher | Subscribers |
|---|---|---|
| `cars/updates` | position_processor | all detector services |
| `meteo/updates` | meteo_consumer | station_assigner |
| `cars/station/<car_id>` | station_assigner | frontend |
| `alerts/<type>` | detector services | frontend |
| `test/cleanup/<car_id>` | pytest conftest | services (state cleanup) |

### Test infrastructure (`tests/`)

Tests are integration tests that require the full stack running (Docker + cloud2edge). Each test:
1. Creates cars via `simulations/create_car.py` (registers in Hono + Ditto)
2. Sends GPS positions via `simulations/send_position.py`
3. Subscribes to the relevant alert topic and asserts the expected alert arrives

`conftest.py` auto-cleans up test cars after each test (sends `(0,0)` position as a cleanup signal, then deletes device files). `test_station_assignment.py` is auto-skipped when `tomastest.com` is unreachable.

### SUMO simulation toolchain

`scripts/bridge.py` — drives SUMO via TraCI, publishes vehicle positions to Ditto over HTTP. Stateless and scenario-agnostic.

`scripts/eval.py` — loads a scenario pack (`simulations/SUMO/scenarios/<pack>/pack.py`), runs each scenario through the bridge, collects alerts from MQTT, and computes precision/recall/F1.

A pack's `pack.py` must export: `SUMOCFG`, `ALERT_TOPIC`, `SCENARIOS` (dict of `ScenarioSpec`), and optional `before_scenario()` hook.

### Speed limit resolution

`overpass_client.py` uses a two-layer approach:
1. **Snapshot** (`data/offline_roads/offline_roads.json`): pre-built tile-indexed road segments with maxspeed
2. **Live fallback**: Overpass API query for any point not covered by the snapshot

`scripts/build_offline_roads.py` builds/updates the snapshot. The position_processor resolves the speed limit once and embeds it in `CarUpdate`, so downstream detectors never call Overpass.

### Alert priority model

`AlertPriority` (LOW=1 … CRITICAL=4) is embedded in every alert payload alongside `expiration_s`. The frontend tracks `current_alert_priority` and only displays/plays alerts with higher priority that are still within their TTL.
