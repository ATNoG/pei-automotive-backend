"""Overtaking detector evaluation scenario pack.

10 controlled SUMO scenarios that evaluate the ``overtaking_detector`` service
running in production (accessible via MQTT).
The pack uses a dedicated 2-lane straight highway network
(simulations/SUMO/scenarios/overtaking/network/overtaking.net.xml, ~508 m,
heading east ~90°). Two lanes are required because SUMO's car-following model
on a single-lane road prevents physical overtaking, so no sign flip ever
reaches the detector.  On the 2-lane road the fast car travels in the passing
lane (lane 1) and the slow car travels in the normal lane (lane 0); SUMO does
not apply car-following between different lanes, so the fast car passes freely.

"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import paho.mqtt.client as mqtt


PACK_DIR = Path(__file__).resolve().parent

# What eval.py reads
SUMOCFG          = PACK_DIR / "network" / "overtaking.sumocfg"
ALERT_TOPIC      = "alerts/overtaking"
ALERT_TIMEOUT_S  = 25.0
END_TIME_S       = 120.0
STEP_LENGTH_S    = 0.5
POST_SIM_DRAIN_S = 8.0
WORKERS          = 4
REAL_TIME        = True


# ScenarioSpec
@dataclass(frozen=True)
class ScenarioSpec:
    """Immutable description of one overtaking evaluation scenario."""

    scenario_id:      str
    description:      str
    expected_outcome: str   # "overtaking" | "no_event"
    route_file:       Path


def _spec(sid: str, desc: str, outcome: str) -> ScenarioSpec:
    return ScenarioSpec(
        scenario_id=sid,
        description=desc,
        expected_outcome=outcome,
        route_file=PACK_DIR / "scenarios" / f"scenario_{sid}.rou.xml",
    )


SCENARIOS: dict[str, ScenarioSpec] = {
    "01": _spec("01", "Classic: 100 km/h (lane 1) overtakes 40 km/h (lane 0), 30 m gap", "overtaking"),
    "02": _spec("02", "Delayed: 100 km/h starts 40 m behind 50 km/h, still overtakes", "overtaking"),
    "03": _spec("03", "Sequential: 120 km/h overtakes 40 km/h then 60 km/h (two events)", "overtaking"),
    "04": _spec("04", "Equal speed (80 km/h), 30 m gap - no sign flip, no event", "no_event"),
    "05": _spec("05", "FP probe: fast car already 30 m ahead - sign never +1, no flip", "no_event"),
    "06": _spec("06", "FP probe: same lane, lane-change blocked - car-following holds back", "no_event"),
    "07": _spec("07", "Extreme diff: 120 km/h passes 40 km/h, 20 m gap (~0.9 s pass)", "overtaking"),
    "08": _spec("08", "FN probe: merging-car departs 10 s late at same speed - never in range","no_event"),
    "09": _spec("09", "Near-threshold: 70 km/h passes 60 km/h, slow 10.8 s pass", "overtaking"),
    "10": _spec("10", "FP probe: side-by-side same speed - no longitudinal flip", "no_event"),
}


# Alert normalisation hook (called by eval.py)
def interpret_alert(alert: dict | None) -> str:
    """Translate a raw overtaking_detector alert into an outcome string."""
    if alert is None:
        return "no_event"
    if alert.get("alert_type") == "overtaking_event":
        return "overtaking"
    return "no_event"

# Positive class for metrics (used by eval.py)
# Explicitly tell eval.py which outcome class is "positive" (event detected)
# to avoid automatic detection failures when running only negative scenarios.
POSITIVE_CLASS = "overtaking"

# Cleanup hook
_CLEANUP_CAR_IDS   = ["sumo-merging-car", "sumo-main-car", "sumo-main-car-2"]
_CLEANUP_TOPIC     = "cars/updates"
_CLEANUP_MQTT_HOST = "localhost"
_CLEANUP_MQTT_PORT = 1884

_logger = logging.getLogger(__name__)


def before_scenario(scenario_id: str) -> None:
    """Reset overtaking_detector in-memory state between scenarios."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    try:
        client.connect(_CLEANUP_MQTT_HOST, _CLEANUP_MQTT_PORT)
        client.loop_start()
        for car_id in _CLEANUP_CAR_IDS:
            payload = json.dumps({"_test_cleanup": True, "car_id": car_id})
            client.publish(_CLEANUP_TOPIC, payload, qos=1)
        time.sleep(0.5)
    except Exception as exc:
        _logger.warning("Detector cleanup failed for scenario %s: %s", scenario_id, exc)
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
