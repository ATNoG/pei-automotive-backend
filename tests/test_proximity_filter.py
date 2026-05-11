"""
Proximity filter tests.

The proximity_filter sits transparently in front of the detector-facing
``cars/updates`` topic: position_processor publishes to ``cars/raw_updates``,
the proximity_filter enriches each update with the tile_quadkey computed
via Igor's QuadTree encoding and republishes to ``cars/updates``. Every
detector benefits without code changes.

Coverage:

1. Unit tests for the geotile primitives (no infrastructure required).
2. Unit tests for the proximity_filter's enrich-and-forward logic, using
   a stub MQTT client so the routing semantics are validated locally.
3. An end-to-end integration test that runs two simultaneous overtaking
   maneuvers in completely different tiles (Aveiro vs. Lisbon) and
   verifies that:
     - Every car update reaching the detectors carries the injected
       ``tile_quadkey`` / ``tile_zoom`` fields.
     - Each pair triggers its own overtaking alert.
     - No alert ever pairs a car from one tile with a car from the other.
"""
import importlib.util
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from threading import Thread

import paho.mqtt.client as mqtt

# Allow `from common.geotile import ...` when running from tests/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from common.geotile import MAX_ZOOM, get_quadkey, get_tile_bounds  # noqa: E402

from helpers import (  # noqa: E402
    MQTT_HOST, MQTT_PORT,
    ensure_car_exists, send_position, standalone_get_car_id,
)


# A point in Aveiro, PT.
LAT, LON = 40.6405, -8.6538


# ---------------------------------------------------------------------------
# Unit tests for the geotile primitives (no infrastructure needed)
# ---------------------------------------------------------------------------

def test_quadkey_is_deterministic():
    assert get_quadkey(LAT, LON, MAX_ZOOM) == get_quadkey(LAT, LON, MAX_ZOOM)


def test_quadkey_changes_with_position():
    base = get_quadkey(LAT, LON, MAX_ZOOM)
    # 0.001 degrees ~ 100m, plenty to cross a zoom-31 tile.
    assert get_quadkey(LAT + 0.001, LON, MAX_ZOOM) != base
    assert get_quadkey(LAT, LON + 0.001, MAX_ZOOM) != base


def test_tile_bounds_are_strictly_increasing():
    for zoom in (5, 10, 15, 20, 25):
        lower, upper = get_tile_bounds(LAT, LON, zoom, MAX_ZOOM)
        assert lower < upper, f"bounds not increasing at zoom {zoom}"


def test_max_zoom_key_falls_inside_tile_bounds():
    # The point's zoom-MAX_ZOOM key must satisfy lower <= key < upper for
    # every coarser zoom that contains it.
    point_qk = get_quadkey(LAT, LON, MAX_ZOOM)
    for zoom in range(1, MAX_ZOOM):
        lower, upper = get_tile_bounds(LAT, LON, zoom, MAX_ZOOM)
        assert lower <= point_qk < upper, (
            f"point {point_qk} outside [{lower}, {upper}) at zoom {zoom}"
        )


def test_distant_point_is_outside_tile_bounds():
    # Aveiro tile bounds at zoom 15 should NOT contain a point in Lisbon.
    lower, upper = get_tile_bounds(LAT, LON, 15, MAX_ZOOM)
    lisbon = get_quadkey(38.7223, -9.1393, MAX_ZOOM)
    assert not (lower <= lisbon < upper)


def test_aveiro_and_lisbon_are_in_different_tiles_at_routing_zoom():
    # The integration test below relies on this: at the routing zoom used by
    # the proximity_filter (15), the Aveiro and Lisbon test pairs must land
    # in different tiles, otherwise the test wouldn't exercise the filter.
    routing_zoom = 15
    assert get_quadkey(40.6405, -8.6538, routing_zoom) != get_quadkey(
        38.7223, -9.1393, routing_zoom
    )


# ---------------------------------------------------------------------------
# Unit tests for the proximity_filter's enrich-and-forward routing
# ---------------------------------------------------------------------------

def _load_proximity_filter_module():
    """Import the proximity_filter service as a standalone module."""
    path = Path(__file__).resolve().parent.parent / "src" / "services" / "proximity_filter" / "service.py"
    spec = importlib.util.spec_from_file_location("proximity_filter_service", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _StubMqtt:
    """Captures publish calls so we can assert on the rewritten payload."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str, qos: int = 1, retain: bool = False):
        self.published.append((topic, payload))


class _StubConfig:
    car_updates_topic = "cars/updates"
    raw_car_updates_topic = "cars/raw_updates"
    in_scope_topic = "cars/in_scope"
    broker_host = "localhost"
    broker_port = 1883
    broker_user = None
    broker_password = None


def _make_filter(zoom: int = 15):
    pf_module = _load_proximity_filter_module()
    instance = pf_module.ProximityFilter.__new__(pf_module.ProximityFilter)
    instance.config = _StubConfig()
    instance.proximity_zoom = zoom
    instance.mqtt = _StubMqtt()
    instance.cars_by_tile = defaultdict(set)
    instance.car_tile = {}
    return instance


def test_proximity_filter_injects_tile_quadkey():
    pf = _make_filter(zoom=15)
    payload = json.dumps({
        "car_id": "x", "latitude": LAT, "longitude": LON,
        "speed_kmh": 50.0, "heading_deg": 90.0,
    })
    pf._enrich_and_forward(payload)

    assert len(pf.mqtt.published) == 1
    topic, out_payload = pf.mqtt.published[0]
    assert topic == "cars/updates/x"
    parsed = json.loads(out_payload)
    assert parsed["tile_zoom"] == 15
    assert parsed["tile_quadkey"] == get_quadkey(LAT, LON, 15)
    # Original fields preserved
    assert parsed["car_id"] == "x"
    assert parsed["latitude"] == LAT
    assert parsed["longitude"] == LON


def test_proximity_filter_passes_cleanup_sentinel_through_unchanged():
    pf = _make_filter()
    payload = json.dumps({
        "car_id": "x", "latitude": 0.0, "longitude": 0.0,
        "_test_cleanup": True,
    })
    pf._enrich_and_forward(payload)

    topic, out_payload = pf.mqtt.published[0]
    parsed = json.loads(out_payload)
    assert "tile_quadkey" not in parsed
    assert "tile_zoom" not in parsed
    assert parsed["_test_cleanup"] is True


def test_proximity_filter_skips_origin_marker_without_cleanup_flag():
    # An (almost-)origin update without _test_cleanup is also treated as a
    # marker (matches position_processor's sentinel handling).
    pf = _make_filter()
    payload = json.dumps({
        "car_id": "x", "latitude": 0.0, "longitude": 0.0,
        "speed_kmh": None, "heading_deg": None,
    })
    pf._enrich_and_forward(payload)

    topic, out_payload = pf.mqtt.published[0]
    parsed = json.loads(out_payload)
    assert "tile_quadkey" not in parsed


def test_proximity_filter_drops_unparseable_payload():
    pf = _make_filter()
    pf._enrich_and_forward("not-json")
    assert pf.mqtt.published == []


# ---------------------------------------------------------------------------
# Unit tests for cars/in_scope routing
# ---------------------------------------------------------------------------

def test_lone_car_not_published_to_in_scope():
    """A car alone in its tile must only appear on cars/updates, not in_scope."""
    pf = _make_filter(zoom=15)
    payload = json.dumps({
        "car_id": "x", "latitude": LAT, "longitude": LON,
        "speed_kmh": 50.0, "heading_deg": 90.0,
    })
    pf._enrich_and_forward(payload)

    topics = [t for t, _ in pf.mqtt.published]
    assert "cars/updates/x" in topics
    assert not any(t.startswith("cars/in_scope/") for t in topics)


def test_second_car_in_same_tile_triggers_in_scope_for_both():
    """When a second car arrives in the same tile, that update is in_scope.
    Subsequent updates from the first car are also in_scope."""
    pf = _make_filter(zoom=15)
    # Two positions within the same zoom-15 tile (~1.2 km wide).
    payload_a = json.dumps({
        "car_id": "a", "latitude": LAT, "longitude": LON,
        "speed_kmh": 50.0, "heading_deg": 90.0,
    })
    # 0.0001° ≈ 11 m — same tile as LAT, LON at zoom 15.
    payload_b = json.dumps({
        "car_id": "b", "latitude": LAT + 0.0001, "longitude": LON,
        "speed_kmh": 50.0, "heading_deg": 90.0,
    })

    pf._enrich_and_forward(payload_a)
    assert not any(t.startswith("cars/in_scope/") for t, _ in pf.mqtt.published), (
        "first car alone in tile should not be in_scope"
    )

    pf.mqtt.published.clear()
    pf._enrich_and_forward(payload_b)
    topics = [t for t, _ in pf.mqtt.published]
    assert "cars/in_scope/b" in topics, "second car makes tile multi-car, should be in_scope"

    pf.mqtt.published.clear()
    pf._enrich_and_forward(payload_a)
    topics = [t for t, _ in pf.mqtt.published]
    assert "cars/in_scope/a" in topics, "first car update after tile is multi-car should be in_scope"


def test_car_exits_scope_after_tile_drops_to_one():
    """After one car leaves, the remaining solo car is no longer in_scope."""
    pf = _make_filter(zoom=15)
    payload_a = json.dumps({
        "car_id": "a", "latitude": LAT, "longitude": LON,
        "speed_kmh": 50.0, "heading_deg": 90.0,
    })
    payload_b = json.dumps({
        "car_id": "b", "latitude": LAT + 0.0001, "longitude": LON,
        "speed_kmh": 50.0, "heading_deg": 90.0,
    })

    pf._enrich_and_forward(payload_a)
    pf._enrich_and_forward(payload_b)
    pf.mqtt.published.clear()

    # b moves to a completely different tile (Lisbon).
    LISBON_LAT, LISBON_LON = 38.7223, -9.1393
    payload_b_lisbon = json.dumps({
        "car_id": "b", "latitude": LISBON_LAT, "longitude": LISBON_LON,
        "speed_kmh": 50.0, "heading_deg": 90.0,
    })
    pf._enrich_and_forward(payload_b_lisbon)
    pf.mqtt.published.clear()

    # Now a is alone in its tile — should not be in_scope.
    pf._enrich_and_forward(payload_a)
    topics = [t for t, _ in pf.mqtt.published]
    assert not any(t.startswith("cars/in_scope/") for t in topics), (
        "car is alone in tile after partner left, must not appear in_scope"
    )


def test_cleanup_sentinel_forwarded_to_in_scope():
    """_test_cleanup sentinels must reach cars/in_scope so detectors there can clean up."""
    pf = _make_filter()
    payload = json.dumps({
        "car_id": "x", "latitude": 0.0, "longitude": 0.0,
        "_test_cleanup": True,
    })
    pf._enrich_and_forward(payload)

    topics = [t for t, _ in pf.mqtt.published]
    assert "cars/updates/x" in topics
    assert "cars/in_scope/x" in topics


def test_origin_marker_forwarded_to_in_scope():
    """Origin (0,0) markers must reach both topics for state cleanup."""
    pf = _make_filter()
    payload = json.dumps({
        "car_id": "x", "latitude": 0.0, "longitude": 0.0,
        "speed_kmh": None, "heading_deg": None,
    })
    pf._enrich_and_forward(payload)

    topics = [t for t, _ in pf.mqtt.published]
    assert "cars/updates/x" in topics
    assert "cars/in_scope/x" in topics


# ---------------------------------------------------------------------------
# Integration test: two pairs overtaking in different tiles
# ---------------------------------------------------------------------------

ALERTS: list[dict] = []
CAR_UPDATES: list[dict] = []


def _on_overtake_message(_client, _userdata, msg):
    try:
        ALERTS.append(json.loads(msg.payload.decode()))
    except Exception:
        pass


def _on_car_update_message(_client, _userdata, msg):
    try:
        CAR_UPDATES.append(json.loads(msg.payload.decode()))
    except Exception:
        pass


def _drive_pair(slow_id: str, fast_id: str, lat: float, base_lon: float):
    """
    Drive an overtaking pair eastwards starting at (lat, base_lon).

    Slow car sits at base_lon and crawls east; fast car starts ~25m behind,
    catches up, and ends up ~25m ahead, triggering a single overtaking alert.
    """
    DLON_SLOW = 3e-5  # ~2.5 m east per step at lat≈40N
    DLON_FAST = 8e-5  # ~6.8 m east per step
    INITIAL_GAP = 3e-4  # ~25 m
    STEPS = 18

    for i in range(STEPS):
        slow_lon = base_lon + i * DLON_SLOW
        fast_lon = base_lon - INITIAL_GAP + i * DLON_FAST

        t_slow = Thread(target=send_position, args=(slow_id, lat, slow_lon))
        t_fast = Thread(target=send_position, args=(fast_id, lat, fast_lon))
        t_slow.start(); t_fast.start()
        t_slow.join();  t_fast.join()
        time.sleep(0.05)


def test_proximity_filter_end_to_end(get_car_id):
    """
    End-to-end check that the proximity_filter is wired in front of every
    detector and is enriching cars/updates with tile metadata, while the
    detectors themselves stay unchanged. Drives two simultaneous overtaking
    maneuvers in different tiles (Aveiro and Lisbon).

    The default car_id base names below match the entries registered in the
    Android frontend's AppConfig.kt so the maneuver is visible on the map
    when the test is run with --fixed-ids.
    """
    a_slow = get_car_id("prox-aveiro-slow")
    a_fast = get_car_id("prox-aveiro-fast")
    b_slow = get_car_id("prox-lisbon-slow")
    b_fast = get_car_id("prox-lisbon-fast")

    ALERTS.clear()
    CAR_UPDATES.clear()

    for car in (a_slow, a_fast, b_slow, b_fast):
        ensure_car_exists(car)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.message_callback_add("alerts/overtaking/+", _on_overtake_message)
    client.message_callback_add("cars/updates/+", _on_car_update_message)
    client.connect(MQTT_HOST, MQTT_PORT)
    client.subscribe([("alerts/overtaking/+", 1), ("cars/updates/+", 1)])
    client.loop_start()

    # Drive both pairs in parallel.
    aveiro = Thread(target=_drive_pair, args=(a_slow, a_fast, 40.6405, -8.6538))
    lisbon = Thread(target=_drive_pair, args=(b_slow, b_fast, 38.7223, -9.1393))
    aveiro.start(); lisbon.start()
    aveiro.join();  lisbon.join()

    # Let the broker drain the last alerts.
    time.sleep(2)
    client.loop_stop()
    client.disconnect()

    # ---- 1. The proximity_filter enriched every relevant update ----
    test_car_ids = {a_slow, a_fast, b_slow, b_fast}
    relevant_updates = [u for u in CAR_UPDATES if u.get("car_id") in test_car_ids]
    assert relevant_updates, "no car updates were observed on cars/updates"

    missing_tile = [
        u for u in relevant_updates
        if u.get("tile_quadkey") is None or u.get("tile_zoom") is None
    ]
    assert not missing_tile, (
        f"proximity_filter is not enriching every update; "
        f"missing tile_quadkey/zoom on {len(missing_tile)} updates"
    )

    # Aveiro and Lisbon updates must end up with different tile_quadkeys.
    aveiro_qks = {u["tile_quadkey"] for u in relevant_updates if u["car_id"] in (a_slow, a_fast)}
    lisbon_qks = {u["tile_quadkey"] for u in relevant_updates if u["car_id"] in (b_slow, b_fast)}
    assert aveiro_qks and lisbon_qks
    assert aveiro_qks.isdisjoint(lisbon_qks), (
        "Aveiro and Lisbon updates ended up sharing a tile_quadkey "
        "— routing zoom is too coarse for this test"
    )

    # ---- 2. Each pair produced its own overtaking alert ----
    pair_a = {a_fast, a_slow}
    pair_b = {b_fast, b_slow}

    cross_tile_alerts = [
        a for a in ALERTS
        if {a.get("overtaking_car_id"), a.get("overtaken_car_id")} not in (pair_a, pair_b)
    ]
    a_alerts = [
        a for a in ALERTS
        if a.get("overtaking_car_id") == a_fast and a.get("overtaken_car_id") == a_slow
    ]
    b_alerts = [
        a for a in ALERTS
        if a.get("overtaking_car_id") == b_fast and a.get("overtaken_car_id") == b_slow
    ]

    assert not cross_tile_alerts, (
        f"detector compared cars across tiles: {cross_tile_alerts}"
    )
    assert a_alerts, "expected an overtaking alert for the Aveiro pair, got none"
    assert b_alerts, "expected an overtaking alert for the Lisbon pair, got none"


if __name__ == "__main__":
    test_proximity_filter_end_to_end(standalone_get_car_id)
