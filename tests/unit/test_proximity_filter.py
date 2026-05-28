"""
Unit tests for the geotile primitives and the ProximityFilter GPS enrichment logic.
No infrastructure required — ProximityFilter is instantiated with a stubbed MQTT client.
"""
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from common.geotile import MAX_ZOOM, get_quadkey, get_tile_bounds  # noqa: E402


LAT, LON = 40.6405, -8.6538


# ---------------------------------------------------------------------------
# Geotile primitives
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
    ovar = get_quadkey(40.866, -8.627, MAX_ZOOM)
    assert not (lower <= ovar < upper)


def test_aveiro_and_ovar_are_in_different_tiles_at_routing_zoom():
    routing_zoom = 15
    assert get_quadkey(40.6405, -8.6538, routing_zoom) != get_quadkey(
        40.866, -8.627, routing_zoom
    )


# ---------------------------------------------------------------------------
# ProximityFilter GPS enrichment (stubbed MQTT)
# ---------------------------------------------------------------------------

def _load_proximity_filter_module():
    path = (
        Path(__file__).resolve().parent.parent.parent
        / "src" / "services" / "proximity_filter" / "service.py"
    )
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


def test_proximity_filter_aveiro_and_ovar_different_quadkeys():
    pf = _make_filter(zoom=15)
    pf._on_gps_update("a", LAT, LON)
    pf._on_gps_update("b", 40.866, -8.627)

    qk_a = json.loads(pf.mqtt.published[0][1])["tile_quadkey"]
    qk_b = json.loads(pf.mqtt.published[1][1])["tile_quadkey"]
    assert qk_a != qk_b
