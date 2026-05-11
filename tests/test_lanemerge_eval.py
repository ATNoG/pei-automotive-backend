"""
Lane Merge Evaluation - 10 SUMO-driven test cases.

Each test runs a SUMO scenario via the lanemerge runner, collects
alerts/lane_merge output, and asserts the detector's outcome matches
the expected ground truth.  Results are accumulated in the module-level
RESULTS list for the final metrics report.

Run all cases:
    pytest tests/test_lanemerge_eval.py -v

Run one case:
    pytest tests/test_lanemerge_eval.py::test_scenario_01 -v
"""
from __future__ import annotations

import json
import queue
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "simulations" / "lanemerge_eval"))

from ground_truth import SCENARIOS, ScenarioSpec  # noqa: E402

MQTT_HOST = "localhost"
MQTT_PORT = 1884
ALERT_TOPIC = "alerts/lane_merge"
ALERT_TIMEOUT = 15.0   # seconds to wait for an alert after the simulation ends


# ---------------------------------------------------------------------------
# Module-level result accumulation (used by eval_lanemerge.py)
# ---------------------------------------------------------------------------
RESULTS: list[dict] = []   # [{scenario_id, expected, actual, correct}]


# ---------------------------------------------------------------------------
# MQTT alert collector context manager
# ---------------------------------------------------------------------------
@contextmanager
def mqtt_alert_collector(topic: str = ALERT_TOPIC):
    """Subscribe to the lane_merge alert topic and collect payloads."""
    alert_queue: queue.Queue = queue.Queue()

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            alert_queue.put(payload)
        except Exception:
            pass

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    try:
        client.on_message = on_message
        client.connect(MQTT_HOST, MQTT_PORT)
        client.subscribe(topic, qos=1)
        client.loop_start()
        time.sleep(0.3)
        yield alert_queue
    finally:
        client.loop_stop()
        client.disconnect()


def collect_first_alert(alert_queue: queue.Queue, timeout: float = ALERT_TIMEOUT) -> Optional[dict]:
    """Return the first alert payload received within *timeout* seconds."""
    try:
        return alert_queue.get(timeout=timeout)
    except queue.Empty:
        return None


def run_scenario(scenario_id: str) -> Optional[str]:
    """
    Run the SUMO scenario and return the detected outcome string
    ("safe" | "unsafe") or None if no alert was received.
    """
    from runner import run_scenario as _run

    spec: ScenarioSpec = SCENARIOS[scenario_id]

    with mqtt_alert_collector() as alert_queue:
        _run(scenario_id, spec)
        alert = collect_first_alert(alert_queue)

    if alert is None:
        return None

    return alert.get("status")   # "safe" | "unsafe"


def _record(scenario_id: str, expected: str, actual: Optional[str]) -> None:
    """Append a result entry to the module-level list."""
    RESULTS.append({
        "scenario_id": scenario_id,
        "expected": expected,
        "actual": actual if actual is not None else "no_alert",
        "correct": actual == expected,
    })


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def test_scenario_01():
    """Safe - no main-lane traffic."""
    spec = SCENARIOS["01"]
    actual = run_scenario("01")
    _record("01", spec.expected_outcome, actual)
    assert actual == "safe", f"Expected 'safe', got {actual!r}"


def test_scenario_02():
    """Safe - main-lane car far ahead (160 m past merge)."""
    spec = SCENARIOS["02"]
    actual = run_scenario("02")
    _record("02", spec.expected_outcome, actual)
    assert actual == "safe", f"Expected 'safe', got {actual!r}"


def test_scenario_03():
    """Safe - main-lane car just outside entry zone."""
    spec = SCENARIOS["03"]
    actual = run_scenario("03")
    _record("03", spec.expected_outcome, actual)
    assert actual == "safe", f"Expected 'safe', got {actual!r}"


def test_scenario_04():
    """Safe - merging car very slow, main-lane car passes first."""
    spec = SCENARIOS["04"]
    actual = run_scenario("04")
    _record("04", spec.expected_outcome, actual)
    assert actual == "safe", f"Expected 'safe', got {actual!r}"


def test_scenario_05():
    """Safe - large time gap, both at 60 km/h."""
    spec = SCENARIOS["05"]
    actual = run_scenario("05")
    _record("05", spec.expected_outcome, actual)
    assert actual == "safe", f"Expected 'safe', got {actual!r}"


def test_scenario_06():
    """Unsafe - same speed (80 km/h), small gap (30 m)."""
    spec = SCENARIOS["06"]
    actual = run_scenario("06")
    _record("06", spec.expected_outcome, actual)
    assert actual == "unsafe", f"Expected 'unsafe', got {actual!r}"


def test_scenario_07():
    """Unsafe - merging fast (100 km/h) onto slow main lane (30 km/h)."""
    spec = SCENARIOS["07"]
    actual = run_scenario("07")
    _record("07", spec.expected_outcome, actual)
    assert actual == "unsafe", f"Expected 'unsafe', got {actual!r}"


def test_scenario_08():
    """Unsafe - main-lane car right at merge point."""
    spec = SCENARIOS["08"]
    actual = run_scenario("08")
    _record("08", spec.expected_outcome, actual)
    assert actual == "unsafe", f"Expected 'unsafe', got {actual!r}"


def test_scenario_09():
    """Unsafe - merging slow (20 km/h), fast main-lane car (120 km/h)."""
    spec = SCENARIOS["09"]
    actual = run_scenario("09")
    _record("09", spec.expected_outcome, actual)
    assert actual == "unsafe", f"Expected 'unsafe', got {actual!r}"


def test_scenario_10():
    """Unsafe - two main-lane cars; second one too close."""
    spec = SCENARIOS["10"]
    actual = run_scenario("10")
    _record("10", spec.expected_outcome, actual)
    # At least one unsafe alert must fire (for the close second car)
    assert actual == "unsafe", f"Expected 'unsafe', got {actual!r}"


def test_scenario_11():
    """
    FALSE NEGATIVE - fast car (120 km/h) at 140 m exceeds ENTRY_ZONE_M=100 m.

    The detector only inspects main-lane cars within 100 m of the merge point.
    A car at 140 m doing 120 km/h reaches the merge in ~4.2 s - a real collision
    risk - but is skipped because dist_main_to_merge > ENTRY_ZONE_M.

    This test is expected to FAIL. Fix: increase ENTRY_ZONE_M to at least 150 m.
    """
    spec = SCENARIOS["11"]
    actual = run_scenario("11")
    _record("11", spec.expected_outcome, actual)
    assert actual == "unsafe", (
        f"False negative confirmed: detector said {actual!r}. "
        "A car at 140 m / 120 km/h reaches the merge in ~4.2 s. "
        "Fix: increase ENTRY_ZONE_M in LaneMergeDetector from 100 m to at least 150 m."
    )