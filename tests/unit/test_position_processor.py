import importlib.util
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))


def _load_pp_module():
    path = (
        Path(__file__).resolve().parent.parent.parent
        / "src" / "services" / "position_processor" / "service.py"
    )
    spec = importlib.util.spec_from_file_location("position_processor_service", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _StubMqtt:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str, qos: int = 1):
        self.published.append((topic, payload))


class _StubConfig:
    car_updates_topic = "cars/updates"
    raw_car_updates_topic = "cars/raw_updates"
    broker_host = "localhost"
    broker_port = 1883
    broker_user = None
    broker_password = None


def _make_processor():
    module = _load_pp_module()
    pp = module.PositionProcessor.__new__(module.PositionProcessor)
    pp.config = _StubConfig()
    pp.mqtt = _StubMqtt()
    pp.states = {}
    pp.states_lock = threading.Lock()
    pp._resolve_speed_limit = lambda lat, lon: 50.0
    return pp


def _age_state(pp, car_id, dt_s=0.1):
    if car_id in pp.states:
        lat, lon, ts, h = pp.states[car_id]
        pp.states[car_id] = (lat, lon, ts - dt_s, h)


def _send(pp, car_id, lat, lon):
    _age_state(pp, car_id)
    pp._handle_raw_update(json.dumps({"car_id": car_id, "latitude": lat, "longitude": lon}))
    return json.loads(pp.mqtt.published[-1][1])


LAT = 40.628
LON = -8.732
STEP = 0.0001  # ~11 m


def test_first_position_heading_is_none():
    pp = _make_processor()
    result = _send(pp, "car", LAT, LON)
    assert result["heading_deg"] is None


def test_heading_computed_when_moving():
    pp = _make_processor()
    _send(pp, "car", LAT, LON)
    result = _send(pp, "car", LAT + STEP, LON)
    assert result["heading_deg"] is not None


def test_heading_preserved_when_stopped():
    pp = _make_processor()
    _send(pp, "car", LAT, LON)
    moving = _send(pp, "car", LAT + STEP, LON)
    heading = moving["heading_deg"]
    assert heading is not None

    stopped = _send(pp, "car", LAT + STEP, LON)
    assert stopped["heading_deg"] == heading


def test_heading_preserved_on_fast_update():
    pp = _make_processor()
    _send(pp, "car", LAT, LON)
    moving = _send(pp, "car", LAT + STEP, LON)
    heading = moving["heading_deg"]
    assert heading is not None

    # do NOT age the state so dt < 0.05 is triggered
    pp._handle_raw_update(json.dumps({"car_id": "car", "latitude": LAT + STEP * 2, "longitude": LON}))
    fast = json.loads(pp.mqtt.published[-1][1])
    assert fast["heading_deg"] == heading


def test_heading_updates_when_direction_changes():
    pp = _make_processor()
    _send(pp, "car", LAT, LON)
    north = _send(pp, "car", LAT + STEP, LON)
    east = _send(pp, "car", LAT + STEP, LON + STEP)
    assert north["heading_deg"] is not None
    assert east["heading_deg"] is not None
    assert abs(north["heading_deg"] - east["heading_deg"]) > 45


def test_heading_independent_per_car():
    pp = _make_processor()
    _send(pp, "car-a", LAT, LON)
    _send(pp, "car-b", LAT, LON)
    north_a = _send(pp, "car-a", LAT + STEP, LON)
    east_b = _send(pp, "car-b", LAT, LON + STEP)
    assert north_a["heading_deg"] is not None
    assert east_b["heading_deg"] is not None
    assert abs(north_a["heading_deg"] - east_b["heading_deg"]) > 45