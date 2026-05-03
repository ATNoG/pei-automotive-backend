# Proximity Filter

## What it is

A new microservice (`src/services/proximity_filter`) that sits transparently
in front of the detector-facing `cars/updates` MQTT topic. It exists so that
backend detectors can — now or in the future — stop being active for cars
out of their proximity scope, **without changing any detector code**.

## Architecture

```
position_processor ──▶ cars/raw_updates ──▶ proximity_filter ──▶ cars/updates ──▶ all detectors
                                            (injects               (unchanged)
                                             tile_quadkey,
                                             tile_zoom)
```

- `position_processor` no longer publishes directly to `cars/updates`. It
  publishes to a new private topic `cars/raw_updates`.
- `proximity_filter` is the **only** subscriber of `cars/raw_updates`. For
  every payload it parses, it computes a QuadTree quadkey at a configurable
  zoom (`PROXIMITY_ZOOM`, default 15 — ≈1.2 km tile near 41° N), adds
  `tile_quadkey` + `tile_zoom` to the JSON, and republishes to
  `cars/updates`.
- All detectors (`overtaking_detector`, `speed_detector`,
  `traffic_jam_detector`, `accident_detector`,
  `emergency_vehicle_detector`, `highway_entry_detector`,
  `station_assigner`) keep subscribing to `cars/updates` exactly as before.
  They do not import or reference geotile code.

The QuadTree encoding follows Igor Coelho's specification (recursive 2-bit
quadrants packed into a signed 64-bit integer; max zoom 31). The bit-for-bit
encoding is preserved so a future Ditto-side filter using the same quadkey
will agree with what we publish.

## Files changed

### Backend (`pei-automotive-backend`)

| File | Change |
| --- | --- |
| `src/common/geotile.py` | **new** — `get_quadkey(lat, lng, zoom)` and `get_tile_bounds(lat, lng, tile_zoom, max_zoom=31)`, mirroring Igor's encoding bit-for-bit. |
| `src/common/config.py` | Added `raw_car_updates_topic` field to `AppConfig`. New env var `MQTT_RAW_CAR_UPDATES_TOPIC` (default `cars/raw_updates`). |
| `src/services/position_processor/service.py` | One-line behavioural change: publishes to `config.raw_car_updates_topic` instead of `config.car_updates_topic`. |
| `src/services/proximity_filter/__init__.py` | **new** — empty package marker. |
| `src/services/proximity_filter/service.py` | **new** — the `ProximityFilter` proxy. Subscribes to `raw_car_updates_topic`, enriches with `tile_quadkey`/`tile_zoom`, republishes to `car_updates_topic`. Cleanup sentinels (`_test_cleanup` flag or origin (0, 0) markers) pass through unchanged. |
| `src/services/proximity_filter/Dockerfile` | **new** — same shape as the detector dockerfiles. |
| `docker-compose.yml` | New `proximity_filter` service. Every detector (`speed_detector`, `overtaking_detector`, `emergency_vehicle_detector`, `highway_entry_detector`, `accident_detector`, `traffic_jam_detector`) and `station_assigner` now `depends_on: [..., proximity_filter]` so startup ordering is correct. |

**Detectors are unchanged.** No file under `src/services/<detector>/`
(other than the new `proximity_filter`) has been touched. This is the core
goal of the design: the filter applies to every detector by virtue of
sitting on the topic path, not by per-detector refactoring.

### Frontend (`pei-automotive-frontend`)

| File | Change |
| --- | --- |
| `app/src/main/java/pt/it/automotive/app/config/AppConfig.kt` | Registered the integration-test car ids so the maneuver is rendered like the existing tests. `prox-aveiro-slow` added to `USER_CAR_IDS` (camera-followed); `prox-aveiro-fast`, `prox-lisbon-slow`, `prox-lisbon-fast` added to `OTHER_CAR_IDS`. |

### Tests

| File | Change |
| --- | --- |
| `tests/test_proximity_filter.py` | **new** — 11 tests. |

The test file covers three layers:

1. **Geotile primitives** (no infra) — determinism, position-sensitivity,
   strictly-increasing tile bounds, the prefix-containment invariant
   (a point's zoom-31 quadkey is always inside its tile bounds at any
   coarser zoom), distant-point rejection, and a sanity check that the
   integration test's Aveiro and Lisbon coordinates land in different
   tiles at routing zoom 15.
2. **Proximity filter routing** (no infra, uses a stub MQTT client) —
   `_enrich_and_forward` injects `tile_quadkey`/`tile_zoom` correctly,
   passes `_test_cleanup` sentinels through unmodified, leaves origin
   (0, 0) markers without tile metadata, and silently drops unparseable
   payloads.
3. **End-to-end** (`test_proximity_filter_end_to_end`, requires the
   docker-compose stack up) — drives two simultaneous overtaking
   maneuvers, one in Aveiro and one in Lisbon, in parallel via
   `send_position`. Subscribes to both `cars/updates` and
   `alerts/overtaking` and asserts:
   - **Every** test-car update on `cars/updates` carries
     `tile_quadkey` and `tile_zoom` (proves the proxy is on the path of
     every detector).
   - The Aveiro and Lisbon updates land on disjoint `tile_quadkey`
     sets at the routing zoom.
   - Each pair generated its own overtaking alert (regression).
   - Zero alerts pair a car from Aveiro with a car from Lisbon.

10/10 of the offline tests pass; the end-to-end one requires the stack.

## How to use

- `docker compose up --build` brings up the new service alongside the
  existing ones; `proximity_filter` starts after `position_processor` and
  before any detector.
- The routing zoom is tunable via the `PROXIMITY_ZOOM` env var (default 15).
- The internal raw topic is tunable via `MQTT_RAW_CAR_UPDATES_TOPIC`
  (default `cars/raw_updates`).
- Run the integration test with the stack up:
  `pytest --fixed-ids tests/test_proximity_filter.py::test_proximity_filter_end_to_end`.

## Honest tradeoff

Today the filter **enriches**, it does not **drop**. The reason is asymmetry
between detectors:

- `overtaking_detector`, `traffic_jam_detector`, and the multi-car part of
  `accident_detector` are pair-/cluster-based — they could safely ignore
  cars in other tiles.
- `speed_detector`, `emergency_vehicle_detector`, and
  `highway_entry_detector` are single-car — they must fire even for a
  completely isolated vehicle.

A "drop lone cars" filter would silently break the single-car detectors,
so the current implementation is a transparent enrichment hook. The
`tile_quadkey` is the seam any future per-tile bucketing can hook into,
either inside the `proximity_filter` (with per-detector output topics) or
inside individual multi-car detectors that opt in.

## Next plans

Listed in roughly the order we discussed:

1. **Per-tile bucketing inside multi-car detectors.** Now that
   `tile_quadkey` is on every update reaching `cars/updates`, the
   `overtaking_detector`, `traffic_jam_detector`, and (multi-car path of)
   `accident_detector` can be refactored one at a time to maintain state
   bucketed by tile and only compare cars within the same tile. This is
   the actual performance win — detectors stop being O(N²) over the
   global car set and become O(N²) within a tile. Each refactor is a
   self-contained PR; the proxy means they don't have to land together.
2. **Tile-aware filtering in `proximity_filter` itself.** Once enough
   detectors are tile-aware, the `proximity_filter` could move from
   "enrich everyone" to "drop lone-tile updates for the topic that
   serves multi-car detectors only", e.g. by introducing a second output
   topic `cars/in_scope` that single-car detectors can keep ignoring.
3. **Configurable per-tile zoom per detector.** Different detectors care
   about different radii (a traffic jam is bigger than an overtake).
   Eventually `PROXIMITY_ZOOM` should be a per-detector concern
   (e.g. zoom 16 for overtaking ≈600 m, zoom 13 for traffic jam ≈5 km),
   either via per-output-topic enrichment in the filter or by computing
   the tile lazily inside the detector from the raw lat/lon.
4. **Optional Ditto-side filter.** Igor's original note was about
   storing the geotile in `attributes/geotile` on the Ditto thing and
   filtering with RQL prefix-range queries. We've intentionally **not**
   gone there yet because the cars Ditto is read-only over WebSocket on
   our side (REST is meteo-only). If the supervisors later want
   Ditto-side filtering, the helper functions in `src/common/geotile.py`
   already produce the exact integer encoding Igor described, so a
   future producer (e.g. a small Hono enrichment step or a Ditto
   function) can populate `attributes/geotile` with the same quadkey
   the proximity_filter is computing today, and the WebSocket filter
   string would be a one-line change.
5. **Frontend ergonomics for the test cars.** Right now the four
   `prox-*` ids are hard-coded in `AppConfig.kt` next to the existing
   test car names. Long-term the whole `USER_CAR_IDS` /
   `OTHER_CAR_IDS` registry could move into a config file or be
   replaced by a runtime registration message — out of scope for this
   feature, but worth flagging for whoever owns the frontend config.
