Migration Plan
Step 0 — Fix forward: remove cars/in_scope
Files to change:
src/common/config.py: remove in_scope_topic field + env var loading
src/services/proximity_filter/service.py: remove cars_by_tile, car_tile, _update_tile(), _evict_car(), and all in_scope publishing; the service goes back to simple enrich-and-forward
src/services/overtaking_detector/service.py: revert subscribe from in_scope_topic back to car_updates_topic/+
src/services/traffic_jam_detector/service.py: same
tests/test_proximity_filter.py + tests/test_detector_tile_bucketing.py: remove any in_scope-specific test cases
Step 1 — proximity_filter becomes the Ditto gateway
The proximity_filter replaces the position_processor as the service that connects to Ditto. It:
Instantiates DittoWSClient (taken directly from position_processor’s current init)
On GPS callback: computes tile_quadkey via get_quadkey(lat, lon, PROXIMITY_ZOOM)
Publishes to cars/raw_updates/<car_id> via MQTT:
{
  "car_id": "...",
  "latitude": ...,
  "longitude": ...,
  "emergency": false,
  "tile_quadkey": 12345678,
  "tile_zoom": 15
}
Origin markers (lat≈0, lon≈0) from test cleanup: publish as-is to cars/raw_updates/<car_id> without tile fields, so position_processor can evict state and forward the cleanup sentinel downstream
The proximity_filter is then a service with two connections: Ditto WS (in) + MQTT (out). No MQTT subscription at all.
Step 2 — position_processor becomes a pure MQTT service
Position_processor removes DittoWSClient entirely and instead:
Subscribes to cars/raw_updates/+ via MQTT
Parses the raw JSON: {car_id, lat, lon, emergency, tile_quadkey, tile_zoom}
Runs the existing logic: speed, heading, speed_limit computation from consecutive positions
Builds the CarUpdate and passes through tile_quadkey and tile_zoom
Publishes to cars/updates/<car_id>
Cleanup handling: when it receives an origin marker (lat≈0, lon≈0), it evicts the car’s state (as today) and also publishes a _test_cleanup sentinel to cars/updates/<car_id> so all detectors clean up — exactly the same cleanup flow detectors already have.
Step 3 — Update CarUpdate model
Add two optional fields to CarUpdate:
tile_quadkey: Optional[int] = None
tile_zoom: Optional[int] = None
Update to_dict() to include them, and from_dict() to read them. Since they’re optional with None defaults, this is fully backwards compatible with any test code that constructs CarUpdate objects directly.
Step 4 — Config stays clean
No new topics. raw_car_updates_topic (cars/raw_updates) is still the bridge between proximity_filter and position_processor. Everything else unchanged.
Step 5 — Tests
test_proximity_filter.py: update unit tests to test the new Ditto callback → MQTT publish flow instead of the old MQTT subscribe → MQTT publish flow. The integration test (two overtaking pairs in Aveiro vs Lisbon) stays valid, just verify that cars/raw_updates/<car_id> messages now carry tile_quadkey.
Position_processor tests: update to inject raw GPS JSON into cars/raw_updates/<car_id> and assert that cars/updates/<car_id> output carries tile_quadkey passed through correctly.
test_detector_tile_bucketing.py: no changes needed — these push directly into detector _on_car_update methods, entirely bypassing the pipeline.
Final architecture
Ditto WS
    │
    ▼
proximity_filter          (Ditto WS in, MQTT out)
    │  compute tile_quadkey
    │  publish {car_id, lat, lon, emergency, tile_quadkey, tile_zoom}
    ▼
cars/raw_updates/<car_id>
    │
    ▼
position_processor        (pure MQTT service)
    │  compute speed, heading, speed_limit
    │  pass through tile_quadkey + tile_zoom
    ▼
cars/updates/<car_id>
    │
    ├── overtaking_detector    (cars_by_tile, only checks same tile)
    ├── traffic_jam_detector   (cars_by_tile, only checks same tile)
    ├── accident_detector      (cars_by_tile, only checks same tile)
    ├── emergency_vehicle_detector
    ├── speed_detector
    └── highway_entry_detector
Want me to start implementing? If so, I’d go Step 0 → 1 → 2 → 3 → 5 in order. Steps 0 and 3 are tiny; 1 and 2 are the real work.