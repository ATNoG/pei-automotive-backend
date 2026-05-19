# TODO — Fix B: OSM-derived merge detection

## Goal

Make `lane_merge_detector` work on **every real-world on-ramp / lane
convergence** without per-scenario configuration. Today it needs either
hardcoded GeoJSON routes (`highway.json` + `entering.json`) or a manually
maintained `simulations/roads/merge_zones.json`. Both are scenario fixtures
masquerading as a detector.

The plan below uses infrastructure that already exists in the project — the
offline OSM snapshot (`data/offline_roads/offline_roads.json`) and the
`overpass_client` lookup that `position_processor` is already calling once
per car update. We do **not** add new external dependencies.

---

## Key insight

`overpass_client.get_road_info(lat, lon)` already returns
`(speed_limit_kmh, way_id, highway_type)` for the OSM way nearest a GPS
point — confirmed at `src/services/position_processor/service.py:64`. The
snapshot stores `motorway_link`, `trunk_link`, `primary_link`,
`secondary_link`, `tertiary_link` as separate segments. Those `_link` ways
**are** the on-ramps / slip roads. A merge point is just the endpoint of a
`_link` polyline that touches a non-link drivable way.

This means we can derive **all** merge points in the covered area purely
geometrically from the snapshot already on disk — no new Overpass query, no
schema change to the snapshot file.

---

## Phased plan

### Phase 1 — Expose `way_id` in `CarUpdate` (tiny, ~15 min)

`position_processor` already calls `get_road_info` and discards the
`way_id`. Promote it into the published payload.

1. `src/common/models.py` → add `way_id: Optional[int] = None` to
   `CarUpdate` (and its `from_dict` / `to_dict`).
2. `src/services/position_processor/service.py` → keep the `way_id` from
   `get_road_info` and pass it into the `CarUpdate` it publishes
   (lines 64 and 120-129).
3. Every downstream detector now sees which OSM way each car is on. This
   alone is useful well beyond lane merge (overtaking, traffic jam, etc).

Risk: zero. Optional field, backwards-compatible.

### Phase 2 — Derive merge points from the snapshot at startup (~2-3 h)

Build a small helper that runs once when the lane_merge_detector starts
(or when the snapshot file changes).

1. New module `src/common/merge_points.py`:
   - Reads `data/offline_roads/offline_roads.json` (already loaded by
     `overpass_client`; expose `overpass_client.iter_segments()` if needed
     rather than re-parsing).
   - Algorithm:
     ```
     LINK_TYPES = {motorway_link, trunk_link, primary_link,
                   secondary_link, tertiary_link}
     MAIN_TYPES = driveable - LINK_TYPES
     ENDPOINT_THRESHOLD_M = 10   # how close a link endpoint must be to a
                                 #  main way to count as a merge
     for link in segments where highway_type in LINK_TYPES:
         for endpoint in (link.geometry[0], link.geometry[-1]):
             for main in segments where highway_type in MAIN_TYPES:
                 if point_to_polyline_dist_m(endpoint, main.geometry) < 10:
                     yield MergePoint(
                         lat, lon = endpoint,
                         ramp_way_id = link.way_id,
                         main_way_id = main.way_id,
                         ramp_geometry = link.geometry,
                         main_geometry = main.geometry,
                     )
     ```
   - Cache the result. The Aveiro snapshot is ~thousands of segments, so
     the O(L·M) scan runs once and takes a few seconds at most. If it gets
     painful, bucket segments by a coarse lat/lon grid.
   - Persist as `data/offline_roads/merge_points.json` so the detector
     loads it instantly on subsequent restarts. Rebuild when the snapshot's
     `generated_at` timestamp changes.

2. New CLI for ad-hoc rebuilds: `python scripts/build_merge_points.py`.
   Mirrors `scripts/build_offline_roads.py`.

3. Sanity-check: dump the derived merge points onto a map
   (`data/offline_roads/roads_viewer.html` already exists — extend it to
   overlay merge points). Eyeballing 50-100 of them against OSM is the
   only realistic acceptance test.

Risk: medium. The "endpoint within 10m of a main polyline" heuristic will
have false positives at intersections that aren't really merges (e.g. a
slip road that ends at a roundabout). Tighten by also requiring
heading-alignment (angle between link tangent at endpoint and main tangent
at nearest segment < ~45°). Add this check if Phase 3 surfaces noise.

### Phase 3 — Refactor `lane_merge_detector` to use merge points + way_id (~3-4 h)

Replace the current zone-based architecture wholesale.

1. Delete:
   - The `MergeZone` dataclass.
   - `_load_zones`, `_load_route` (the GeoJSON loader).
   - The 30 m distance-to-route classification in `_classify_car`.
   - `simulations/roads/merge_zones.json`.

2. Add:
   - `self.merge_points: List[MergePoint]` loaded via
     `common.merge_points.load()`.
   - A spatial index over merge points keyed by a coarse lat/lon grid
     (~1 km cells) so per-update lookups stay O(neighbours_in_cell).
   - Per-merge-point state: `merging_cars`, `main_lane_cars`,
     `alerted_pairs` (same shape as today's `MergeZone`, just keyed by
     `(ramp_way_id, main_way_id, lat, lon)` instead of by config name).

3. New classification (replaces `_classify_car`):
   ```
   def classify(update):
       # update.way_id comes from position_processor (Phase 1)
       if update.way_id is None:
           return []   # snapshot didn't cover this area
       nearby_points = spatial_index.near(update.lat, update.lon,
                                          radius_m=ENTRY_ZONE_M)
       for mp in nearby_points:
           if update.way_id == mp.ramp_way_id:
               yield (mp, "merging")
           elif update.way_id == mp.main_way_id:
               yield (mp, "main_lane")
   ```
   A car can be classified for multiple merge points simultaneously
   (e.g. two ramps close together). Process each independently.

4. Collision prediction stays almost identical to today's
   `_predict_collision` — it already walks a polyline. Just pass
   `mp.ramp_geometry` and `mp.main_geometry` instead of zone routes.

5. Cleanup state for cars that drift off all known merge points (use the
   existing `ENTRY_ZONE_M * 2` heuristic).

Risk: medium-high. The detector contract changes (alert payload should
include `way_id`s now instead of `zone` name). Coordinate with frontend if
they ever read those fields — currently they don't, only `status`.

### Phase 4 — Test & cleanup (~2 h)

1. `tests/test_lane_merge.py` should still pass: those cars drive on the
   coordinates that `entering.json` came from, which presumably
   correspond to a real OSM `*_link` way + main way in the snapshot.
   Verify by running the test against the new detector with no zone
   config. If the snapshot doesn't cover that area, extend the snapshot
   (`scripts/build_offline_roads.py`) rather than re-introducing GeoJSON.

2. `tests/test_real_world_scenario.py` and `test_real_world_direct.py`
   should pass for the same reason — IT campus is in the snapshot's
   covered bboxes.

3. Once both pass, delete:
   - `simulations/roads/highway.json`
   - `simulations/roads/entering.json`
   - `simulations/roads/real_world_*.json`
   - `simulations/roads/merge_zones.json`
   - The route-loading code in the test files (they currently read
     these GeoJSONs to drive the cars; replace with hardcoded
     start/end coords + interpolation, OR with a tiny SUMO scenario).

4. Update `simulations/SUMO/scenarios/lanemerge/pack.py` — the SUMO
   network can stay (it just generates car positions); only the
   coordinate-overlap assumption with `highway.json` matters and that
   constraint goes away once the detector reads OSM directly.

5. Update CLAUDE.md row for `lane_merge_detector`:
   "Vehicle merging across lanes at intersection" → "Predicted collision
   at any OSM merge point (`*_link` joining a main way) using snapshot
   road graph + per-car way_id."

---

## What this solves

- ✅ Lane merge detection works **anywhere** the OSM snapshot covers
  (currently the entire Aveiro district). Drive a SUMO scenario on the
  IT campus, the A25, the Ponte da Barra — all detected with the same
  code path, no configuration.
- ✅ Removes 4 GeoJSON files + 1 zone config + ~50 LoC of bespoke
  loading logic.
- ✅ Aligns the detector with the rest of the system: `speed_detector`,
  `position_processor`, the offline_roads snapshot are all OSM-driven
  already. The lane merge detector becomes consistent with them.

## What this does **not** solve (out of scope, document as known limits)

- Areas outside the snapshot. Live Overpass fallback in
  `overpass_client` covers single points but doesn't auto-rebuild merge
  points. For now, expand the snapshot offline via
  `scripts/build_offline_roads.py`.
- Multi-lane merges where the ramp joins one of several parallel main
  ways (the heuristic picks the nearest one; usually fine).
- Roundabouts misclassified as merges. Excluding `junction=roundabout`
  ways in Phase 2 fixes most cases.

---

## Suggested execution order

1. Phase 1 (15 min, unblocks everything; ship it standalone, no other
   changes needed).
2. Phase 2 + sanity-check overlay (half day; produces a JSON file we
   can eyeball before touching the detector).
3. Phase 3 (one focused session — keep the detector running on the old
   zone config in parallel via a feature flag if you want to A/B).
4. Phase 4 cleanup once Phase 3 is verified against the existing tests.

Estimated total: ~1-1.5 days of focused work. The first phase alone
is shippable in one PR.
