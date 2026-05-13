"""Overtaking detection scenario pack.

5 controlled SUMO scenarios evaluating autonomous overtaking manoeuvres via
TraCI.  Mirrors the lanemerge pack interface so that the same eval.py runner
can execute both packs without modification.

Contract
--------
eval.py reads the following top-level names from this module:
  SUMOCFG          – Path to the .sumocfg used as base configuration.
  ALERT_TOPIC      – MQTT topic for publishing overtaking events.
  ALERT_TIMEOUT_S  – Seconds to wait for an MQTT alert after simulation ends.
  END_TIME_S       – Simulation wall-clock end (passed to SUMO via --end).
  STEP_LENGTH_S    – Simulation step granularity.
  POST_SIM_DRAIN_S – Extra seconds to drain MQTT after sim ends.
  WORKERS          – Number of parallel scenario workers.
  REAL_TIME        – Whether to run at real-time pace.
  SCENARIOS        – dict[str, ScenarioSpec] of all scenarios in this pack.
  before_scenario  – Optional hook called before each scenario run.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import paho.mqtt.client as mqtt


PACK_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Pack-level constants (read by eval.py)
# ---------------------------------------------------------------------------
SUMOCFG          = PACK_DIR / "network" / "overtaking.sumocfg"
ALERT_TOPIC      = "alerts/overtaking"
ALERT_TIMEOUT_S  = 20.0
END_TIME_S       = 120.0
STEP_LENGTH_S    = 0.1
POST_SIM_DRAIN_S = 5.0
WORKERS          = 4
REAL_TIME        = True


@dataclass(frozen=True)
class ScenarioSpec:
    """Immutable description of a single overtaking scenario."""

    scenario_id:      str
    description:      str
    expected_outcome: str    # "overtake" | "wait" | "abort"
    route_file:       Path
    ego_id:           str    # TraCI ID of the ego vehicle in this scenario


def _spec(sid: str, desc: str, outcome: str, ego: str) -> ScenarioSpec:
    return ScenarioSpec(
        scenario_id=sid,
        description=desc,
        expected_outcome=outcome,
        route_file=PACK_DIR / "scenarios" / f"scenario_{sid}.rou.xml",
        ego_id=ego,
    )


SCENARIOS: dict[str, ScenarioSpec] = {
    "01": _spec("01", "Single slow vehicle 60 m ahead — clean overtake",          "overtake", "ego_01"),
    "02": _spec("02", "Blocker in left lane — ego waits, then overtakes",          "overtake", "ego_02"),
    "03": _spec("03", "Convoy of two slow vehicles — sequential overtake",         "overtake", "ego_03"),
    "04": _spec("04", "Small speed differential (10 km/h) — marginal overtake",   "overtake", "ego_04"),
    "05": _spec("05", "Oncoming traffic window — timed overtake between gaps",     "overtake", "ego_05"),
}


# ---------------------------------------------------------------------------
# Cleanup hook (reset overtaking_detector in-memory state between scenarios)
# ---------------------------------------------------------------------------
_CLEANUP_CAR_IDS   = ["ego_01", "ego_02", "ego_03", "ego_04", "ego_05",
                       "slow_01", "slow_02", "slow1_03", "slow2_03",
                       "slow_04", "slow_05", "blocker_02", "near_05", "far_05"]
_CLEANUP_TOPIC     = "cars/updates"
_CLEANUP_MQTT_HOST = "localhost"
_CLEANUP_MQTT_PORT = 1884

_logger = logging.getLogger(__name__)


def before_scenario(scenario_id: str) -> None:
    """Publish _test_cleanup messages so the overtaking detector resets state.

    Mirrors lanemerge.pack.before_scenario exactly — same MQTT pattern.
    """
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
