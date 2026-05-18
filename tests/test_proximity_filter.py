"""
Proximity filter tests.

Coverage:

1. Unit tests for the geotile primitives (no infrastructure required).
2. Unit tests for the proximity_filter's _on_gps_update logic, using a stub
   MQTT client so the routing semantics are validated locally.
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
from pathlib import Path
from threading import Thread

import paho.mqtt.client as mqtt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from common.geotile import MAX_ZOOM, get_quadkey, get_tile_bounds  # noqa: E402

from helpers import (  # noqa: E402
    MQTT_HOST, MQTT_PORT,
    ensure_car_exists, send_position, standalone_get_car_id,
)


LAT, LON = 40.6405, -8.6538


# ---------------------------------------------------------------------------
# Unit tests for the geotile primitives (no infrastructure needed)
# ---------------------------------------------------------------------------

def test_quadkey_is_deterministic():
    assert get_quadkey(LAT, LON, MAX_ZOOM) == get_quadkey(LAT, LON, MAX_ZOOM)


def test_quadkey_changes_with_position():
    base = get_quadkey(LAT, LON, MAX_ZOOM)
    assert get_quadkey(LAT + 0.001, LON, MAX_ZOOM) != base
    assert get_quadkey(LAT, LON + 0.001, MAX_ZOOM) != base


def test_tile_bounds_are_strictly_increasing():
    for zoom in (5, 10, 15, 20, 25):
        lower, upper = get_tile_bounds(LAT, LON, zoom, MAX_ZOOM)
        assert lower < upper, f"bounds not increasing at zoom {zoom}"


def test_max_zoom_key_falls_inside_tile_bounds():
    point_qk = get_quadkey(LAT, LON, MAX_ZOOM)
    for zoom in range(1, MAX_ZOOM):
        lower, upper = get_tile_bounds(LAT, LON, zoom, MAX_ZOOM)
        assert lower <= point_qk < upper, (
            f"point {point_qk} outside [{lower}, {upper}) at zoom {zoom}"
        )


def test_distant_point_is_outside_tile_bounds():
    lower, upper = get_tile_bounds(LAT, LON, 15, MAX_ZOOM)
    lisbon = get_quadkey(38.7223, -9.1393, MAX_ZOOM)
    assert not (lower <= lisbon < upper)


def test_aveiro_and_lisbon_are_in_different_tiles_at_routing_zoom():
    routing_zoom = 15
    assert get_quadkey(40.6405, -8.6538, routing_zoom) != get_quadkey(
        38.7223, -9.1393, routing_zoom
    )


# ---------------------------------------------------------------------------
# Unit tests for the proximity_filter's GPS enrichment
# ---------------------------------------------------------------------------

def _load_proximity_filter_module():
    path = Path(__file__).resolve().parent.parent / "src" / "services" / "proximity_filter" / "service.py"
    spec = importlib.util.spec_from_file_location("proximity_filter_service", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _StubMqtt:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str, qos: int = 1, retain: bool = False):
        self.published.append((topic, payload))


class _StubConfig:
    car_updates_topic = "cars/updates"
    raw_car_updates_topic = "cars/raw_updates"
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
    return instance


def test_proximity_filter_injects_tile_quadkey():
    pf = _make_filter(zoom=15)
    pf._on_gps_update("x", LAT, LON)

    assert len(pf.mqtt.published) == 1
    topic, out_payload = pf.mqtt.published[0]
    assert topic == "cars/raw_updates/x"
    parsed = json.loads(out_payload)
    assert parsed["tile_zoom"] == 15
    assert parsed["tile_quadkey"] == get_quadkey(LAT, LON, 15)
    assert parsed["car_id"] == "x"
    assert parsed["latitude"] == LAT
    assert parsed["longitude"] == LON
    assert parsed["emergency"] is False


def test_proximity_filter_preserves_emergency_flag():
    pf = _make_filter()
    pf._on_gps_update("ev", LAT, LON, emergency=True)

    _, out_payload = pf.mqtt.published[0]
    assert json.loads(out_payload)["emergency"] is True


def test_proximity_filter_different_cars_different_topics():
    pf = _make_filter()
    pf._on_gps_update("car-a", LAT, LON)
    pf._on_gps_update("car-b", LAT + 0.001, LON)

    topics = [t for t, _ in pf.mqtt.published]
    assert "cars/raw_updates/car-a" in topics
    assert "cars/raw_updates/car-b" in topics


def test_proximity_filter_aveiro_and_lisbon_different_quadkeys():
    pf = _make_filter(zoom=15)
    pf._on_gps_update("a", LAT, LON)
    pf._on_gps_update("b", 38.7223, -9.1393)

    qk_a = json.loads(pf.mqtt.published[0][1])["tile_quadkey"]
    qk_b = json.loads(pf.mqtt.published[1][1])["tile_quadkey"]
    assert qk_a != qk_b


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
    """Drive an overtaking pair eastwards, triggering a single overtaking alert."""
    DLON_SLOW = 3e-5
    DLON_FAST = 8e-5
    INITIAL_GAP = 3e-4
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
    End-to-end check that the proximity_filter enriches cars/updates with tile
    metadata. Drives two simultaneous overtaking maneuvers in different tiles
    (Aveiro and Lisbon) and verifies tile isolation.
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

    aveiro = Thread(target=_drive_pair, args=(a_slow, a_fast, 40.6405, -8.6538))
    lisbon = Thread(target=_drive_pair, args=(b_slow, b_fast, 38.7223, -9.1393))
    aveiro.start(); lisbon.start()
    aveiro.join();  lisbon.join()

    time.sleep(2)
    client.loop_stop()
    client.disconnect()

    test_car_ids = {a_slow, a_fast, b_slow, b_fast}
    relevant_updates = [u for u in CAR_UPDATES if u.get("car_id") in test_car_ids]
    assert relevant_updates, "no car updates were observed on cars/updates"

    missing_tile = [
        u for u in relevant_updates
        if u.get("tile_quadkey") is None or u.get("tile_zoom") is None
    ]
    assert not missing_tile, (
        f"proximity_filter not enriching every update; "
        f"missing tile_quadkey/zoom on {len(missing_tile)} updates"
    )

    aveiro_qks = {u["tile_quadkey"] for u in relevant_updates if u["car_id"] in (a_slow, a_fast)}
    lisbon_qks = {u["tile_quadkey"] for u in relevant_updates if u["car_id"] in (b_slow, b_fast)}
    assert aveiro_qks and lisbon_qks
    assert aveiro_qks.isdisjoint(lisbon_qks), (
        "Aveiro and Lisbon updates share a tile_quadkey — routing zoom too coarse"
    )

    pair_a = {a_fast, a_slow}
    pair_b = {b_fast, b_slow}

    # Only consider alerts where BOTH cars belong to this test
    own_alerts = [
        a for a in ALERTS
        if (
            a.get("overtaking_car_id") in test_car_ids
            and a.get("overtaken_car_id") in test_car_ids
            and {a.get("overtaking_car_id"), a.get("overtaken_car_id")} not in (pair_a, pair_b)
        )
    ]
    a_alerts = [
        a for a in own_alerts
        if a.get("overtaking_car_id") == a_fast and a.get("overtaken_car_id") == a_slow
    ]
    b_alerts = [
        a for a in own_alerts
        if a.get("overtaking_car_id") == b_fast and a.get("overtaken_car_id") == b_slow
    ]

    assert not cross_tile_alerts, f"detector compared cars across tiles: {cross_tile_alerts}"
    assert a_alerts, "expected an overtaking alert for the Aveiro pair, got none"
    assert b_alerts, "expected an overtaking alert for the Lisbon pair, got none"


if __name__ == "__main__":
    test_proximity_filter_end_to_end(standalone_get_car_id)
