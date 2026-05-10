"""
Unit tests for the tile bucketing inside detectors (Step 3).

These tests bypass MQTT entirely: they instantiate each detector with a
stubbed broker, push raw JSON payloads through `_on_car_update`, and assert
on the internal bucket maps and on the alerts the stub captured.

Goal: prove that
  - Cars with `tile_quadkey` end up in the right tile bucket.
  - Cars moving across tiles get evicted from the old bucket.
  - Cross-tile pairs do NOT trigger alerts (overtaking & EV).
  - Cluster searches (traffic jam) only inspect the reference car's tile.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

# Make `from common.* import ...` work when imported from tests/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------

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


def _load_module(rel_path: str, name: str):
    path = Path(__file__).resolve().parent.parent / "src" / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules so dataclasses defined inside the module can
    # resolve type annotations via cls.__module__.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _make_detector(rel_path: str, module_name: str, class_name: str):
    module = _load_module(rel_path, module_name)
    cls = getattr(module, class_name)
    instance = cls.__new__(cls)
    instance.config = _StubConfig()
    instance.mqtt = _StubMqtt()
    # Re-run __init__ logic that sets up state without touching MQTT.
    cls.__init__(instance, _StubConfig())
    instance.mqtt = _StubMqtt()  # replace the real client created in __init__
    return instance


# Two tiles far enough apart that any reasonable proximity check fails:
# Aveiro and Lisbon. Tile zoom 15 keeps them in different tiles.
AVEIRO = (40.6405, -8.6538)
LISBON = (38.7223, -9.1393)


def _update(car_id: str, lat: float, lon: float, *, tile_qk: int | None,
            speed_kmh: float = 50.0, heading_deg: float = 90.0,
            speed_limit_kmh: float = 50.0, emergency: bool = False) -> str:
    payload = {
        "car_id": car_id,
        "latitude": lat,
        "longitude": lon,
        "speed_kmh": speed_kmh,
        "heading_deg": heading_deg,
        "speed_limit_kmh": speed_limit_kmh,
        "emergency": emergency,
        "timestamp": 0.0,
    }
    if tile_qk is not None:
        payload["tile_quadkey"] = tile_qk
        payload["tile_zoom"] = 15
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# Overtaking detector
# ---------------------------------------------------------------------------

def test_overtaking_buckets_cars_by_tile():
    det = _make_detector(
        "services/overtaking_detector/service.py",
        "overtaking_service",
        "OvertakingDetector",
    )

    det._on_car_update(_update("a", *AVEIRO, tile_qk=111))
    det._on_car_update(_update("b", *LISBON, tile_qk=222))

    assert det.car_tile == {"a": 111, "b": 222}
    assert set(det.cars_by_tile[111].keys()) == {"a"}
    assert set(det.cars_by_tile[222].keys()) == {"b"}


def test_overtaking_evicts_when_car_changes_tile():
    det = _make_detector(
        "services/overtaking_detector/service.py",
        "overtaking_service",
        "OvertakingDetector",
    )

    det._on_car_update(_update("a", *AVEIRO, tile_qk=111))
    det._on_car_update(_update("a", *LISBON, tile_qk=222))

    assert det.car_tile["a"] == 222
    assert "a" not in det.cars_by_tile.get(111, {})
    assert "a" in det.cars_by_tile[222]


def test_overtaking_does_not_compare_cars_across_tiles():
    """Two cars in different tiles must never produce an overtake alert."""
    det = _make_detector(
        "services/overtaking_detector/service.py",
        "overtaking_service",
        "OvertakingDetector",
    )

    # Drive a synthetic "overtake" (sign flip) but with cars in different tiles.
    det._on_car_update(_update("a", *AVEIRO, tile_qk=111, heading_deg=90))
    det._on_car_update(_update("b", *LISBON, tile_qk=222, heading_deg=90))
    det._on_car_update(_update("a", *AVEIRO, tile_qk=111, heading_deg=90))
    det._on_car_update(_update("b", *LISBON, tile_qk=222, heading_deg=90))

    assert det.mqtt.published == [], (
        f"expected no alerts across tiles, got {det.mqtt.published}"
    )


# ---------------------------------------------------------------------------
# Emergency vehicle detector
# ---------------------------------------------------------------------------

def test_ev_detector_buckets_cars_by_tile():
    det = _make_detector(
        "services/emergency_vehicle_detector/service.py",
        "ev_service",
        "EVDetector",
    )

    det._on_car_update(_update("regular", *AVEIRO, tile_qk=111))
    det._on_car_update(_update("ev", *LISBON, tile_qk=222, emergency=True))

    assert det.car_tile == {"regular": 111, "ev": 222}


def test_ev_detector_skips_regular_cars_in_other_tiles():
    """An EV update should only check cars in its own tile bucket."""
    det = _make_detector(
        "services/emergency_vehicle_detector/service.py",
        "ev_service",
        "EVDetector",
    )

    # Regular car in Aveiro, EV updates in Lisbon — no alert expected,
    # even though without bucketing the haversine distance check would
    # already filter them out (this is a defense-in-depth check).
    det._on_car_update(_update("regular", *AVEIRO, tile_qk=111))
    det._on_car_update(_update("ev", *LISBON, tile_qk=222, emergency=True))

    assert det.mqtt.published == []


# ---------------------------------------------------------------------------
# Traffic jam detector
# ---------------------------------------------------------------------------

def test_traffic_jam_buckets_cars_by_tile():
    det = _make_detector(
        "services/traffic_jam_detector/service.py",
        "traffic_jam_service",
        "TrafficJamDetector",
    )

    det._on_car_update(
        _update("a", *AVEIRO, tile_qk=111, speed_kmh=5.0, speed_limit_kmh=50.0)
    )
    det._on_car_update(
        _update("b", *LISBON, tile_qk=222, speed_kmh=5.0, speed_limit_kmh=50.0)
    )

    assert det.car_tile == {"a": 111, "b": 222}
    assert "a" in det.cars_by_tile[111]
    assert "b" in det.cars_by_tile[222]


def test_traffic_jam_cluster_search_stays_in_tile():
    """`_find_jam_cluster` must only see cars sharing the reference tile."""
    det = _make_detector(
        "services/traffic_jam_detector/service.py",
        "traffic_jam_service",
        "TrafficJamDetector",
    )

    # 5 slow cars in tile 111 (would form a jam) and 5 in tile 222.
    for i in range(5):
        det._on_car_update(_update(
            f"a{i}", AVEIRO[0] + i * 1e-5, AVEIRO[1], tile_qk=111,
            speed_kmh=5.0, speed_limit_kmh=50.0,
        ))
        det._on_car_update(_update(
            f"b{i}", LISBON[0] + i * 1e-5, LISBON[1], tile_qk=222,
            speed_kmh=5.0, speed_limit_kmh=50.0,
        ))

    cluster = det._find_jam_cluster(det.cars["a0"])
    cluster_ids = {c.car_id for c in cluster}
    assert cluster_ids.issubset({f"a{i}" for i in range(5)}), (
        f"cluster leaked across tiles: {cluster_ids}"
    )
