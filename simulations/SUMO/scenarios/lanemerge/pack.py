"""Lane merge detection scenario pack.

10 controlled SUMO scenarios for evaluating the lane_merge_detector service.
Each scenario has a known expected outcome derived from the physics of the
setup (speeds and initial positions).

This file is the contract eval.py reads. To add a new pack, create a sibling
folder under simulations/SUMO/scenarios/ with a pack.py exposing the same
top-level names.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import paho.mqtt.client as mqtt


PACK_DIR = Path(__file__).resolve().parent

# What eval.py reads
SUMOCFG          = PACK_DIR / "network" / "lanemerge.sumocfg"
ALERT_TOPIC      = "alerts/lane_merge/+"
ALERT_TIMEOUT_S  = 20.0
END_TIME_S       = 120.0
STEP_LENGTH_S    = 0.5
POST_SIM_DRAIN_S = 5.0
WORKERS          = 4
REAL_TIME        = True


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    description: str
    expected_outcome: str   # "safe" | "unsafe"
    route_file: Path


def _spec(sid: str, desc: str, outcome: str) -> ScenarioSpec:
    return ScenarioSpec(
        scenario_id=sid,
        description=desc,
        expected_outcome=outcome,
        route_file=PACK_DIR / "scenarios" / f"scenario_{sid}.rou.xml",
    )


SCENARIOS: dict[str, ScenarioSpec] = {
    "01": _spec("01", "No main-lane traffic - merging car enters freely", "safe"),
    "02": _spec("02", "Main-lane car far ahead (160 m past merge) - safe gap", "safe"),
    "03": _spec("03", "Main-lane car just outside entry zone (110 m past merge)", "safe"),
    "04": _spec("04", "Merging car very slow (20 km/h) - main-lane car passes first", "safe"),
    "05": _spec("05", "Large time gap (200 m behind merge) - both at 60 km/h", "safe"),
    "06": _spec("06", "Same speed (80 km/h), ~30 m gap at trigger - collision course", "unsafe"),
    "07": _spec("07", "Both 60 km/h, ~30 m gap at trigger - immediate conflict", "unsafe"),
    "08": _spec("08", "Merging car slow (40 km/h), fast main-lane car (120 km/h) approaching", "unsafe"),
    "09": _spec("09", "Two main-lane cars: first passes safely, second too close", "unsafe"),
    "10": _spec("10", "Fast main-lane car (120 km/h) 140 m out - beyond ENTRY_ZONE_M cutoff", "unsafe"),
}


# Hook eval.py calls before each scenario. Resets lane_merge_detector's
# in-memory state (cars / main_lane_cars / merging_cars / alerted_pairs) by
# publishing _test_cleanup messages. Without this, stale state from a
# previous scenario causes false alerts.
_CLEANUP_CAR_IDS  = ["sumo-merging-car", "sumo-main-car", "sumo-main-car-2"]
_CLEANUP_TOPIC    = "cars/updates/+"
_CLEANUP_MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
_CLEANUP_MQTT_PORT = int(os.getenv("MQTT_PORT", "1884"))

_logger = logging.getLogger(__name__)


def before_scenario(scenario_id: str) -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    try:
        client.connect(_CLEANUP_MQTT_HOST, _CLEANUP_MQTT_PORT)
        client.loop_start()
        for car_id in _CLEANUP_CAR_IDS:
            payload = json.dumps({"_test_cleanup": True, "car_id": car_id})
            client.publish(_CLEANUP_TOPIC, payload, qos=1)
        time.sleep(0.5)
    except Exception as e:
        _logger.warning("Detector cleanup failed for scenario %s: %s", scenario_id, e)
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
