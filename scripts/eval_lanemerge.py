"""
Lane Merge Detector — performance evaluation script.

Runs all 10 SUMO scenarios end-to-end, compares the detector's alert
output against ground truth, and reports precision / recall / F1 both
to stdout and as a JSON file.

Prerequisites
-------------
- MQTT broker running on MQTT_HOST:MQTT_PORT (default localhost:1884)
- LaneMergeDetector service running and subscribed to cars/updates
- SUMO binary on PATH (optional — falls back to kinematic simulation)

Usage
-----
    python scripts/eval_lanemerge.py
    python scripts/eval_lanemerge.py --output results/eval_$(date +%Y%m%d).json
    python scripts/eval_lanemerge.py --scenarios 06 07 08   # subset
"""
from __future__ import annotations

import argparse
import json
import logging
import queue
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

# Make the eval modules importable regardless of cwd
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "simulations" / "lanemerge_eval"))
sys.path.insert(0, str(_REPO_ROOT / "src"))

from ground_truth import SCENARIOS, ScenarioSpec   # noqa: E402
from runner import run_scenario as _runner_run     # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger("eval_lanemerge")

# ---------------------------------------------------------------------------
# Configuration (mirror test_lanemerge_eval.py so the same broker is used)
# ---------------------------------------------------------------------------
MQTT_HOST    = "localhost"
MQTT_PORT    = 1884
ALERT_TOPIC  = "alerts/lane_merge"
ALERT_TIMEOUT = 20.0   # seconds — generous to cover slow machines / CI

DEFAULT_OUTPUT = _REPO_ROOT / "results" / "lanemerge_eval.json"

ALL_SCENARIO_IDS = [f"{i:02d}" for i in range(1, 11)]


# ---------------------------------------------------------------------------
# MQTT helpers
# ---------------------------------------------------------------------------
@contextmanager
def _alert_collector(topic: str = ALERT_TOPIC):
    """Yield a Queue that receives every alert published on *topic*."""
    q: queue.Queue = queue.Queue()

    def _on_message(client, userdata, msg):
        try:
            q.put(json.loads(msg.payload.decode()))
        except Exception:
            pass

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    try:
        client.on_message = _on_message
        client.connect(MQTT_HOST, MQTT_PORT)
        client.subscribe(topic, qos=1)
        client.loop_start()
        time.sleep(0.3)
        yield q
    finally:
        client.loop_stop()
        client.disconnect()


def _collect_first(q: queue.Queue, timeout: float) -> Optional[dict]:
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


# ---------------------------------------------------------------------------
# Per-scenario evaluation
# ---------------------------------------------------------------------------
def evaluate_scenario(scenario_id: str) -> dict:
    """
    Run one scenario, return a result dict:
        {scenario_id, description, expected, actual, correct, alert_payload}
    """
    spec: ScenarioSpec = SCENARIOS[scenario_id]
    logger.info(f"[{scenario_id}] {spec.description}")

    with _alert_collector() as alert_queue:
        _runner_run(scenario_id, spec)
        alert = _collect_first(alert_queue, ALERT_TIMEOUT)

    actual = alert.get("status") if alert else None

    return {
        "scenario_id":  scenario_id,
        "description":  spec.description,
        "expected":     spec.expected_outcome,
        "actual":       actual if actual is not None else "no_alert",
        "correct":      actual == spec.expected_outcome,
        "alert_payload": alert,
    }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def compute_metrics(results: list[dict]) -> dict:
    """
    Binary classification metrics with 'unsafe' as the positive class.
    """
    tp = sum(1 for r in results if r["expected"] == "unsafe" and r["actual"] == "unsafe")
    fp = sum(1 for r in results if r["expected"] == "safe"   and r["actual"] == "unsafe")
    tn = sum(1 for r in results if r["expected"] == "safe"   and r["actual"] == "safe")
    fn = sum(1 for r in results if r["expected"] == "unsafe" and r["actual"] != "unsafe")

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1        = (2 * precision * recall / (precision + recall)
                 if (precision + recall) > 0 else 0.0)
    accuracy  = (tp + tn) / len(results) if results else 0.0

    return {
        "true_positives":  tp,
        "false_positives": fp,
        "true_negatives":  tn,
        "false_negatives": fn,
        "precision":       round(precision, 4),
        "recall":          round(recall,    4),
        "f1_score":        round(f1,        4),
        "accuracy":        round(accuracy,  4),
    }


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
_PASS = "PASS"
_FAIL = "FAIL"
_COL  = 60   # description column width


def print_report(results: list[dict], metrics: dict) -> None:
    total = len(results)
    passed = sum(1 for r in results if r["correct"])

    print()
    print("=" * 80)
    print("  Lane Merge Detector — Evaluation Report")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 80)
    header = f"  {'ID':<4} {'Expected':<10} {'Actual':<12} {'':6} Description"
    print(header)
    print("-" * 80)

    for r in results:
        tag  = _PASS if r["correct"] else _FAIL
        desc = r["description"]
        if len(desc) > _COL:
            desc = desc[:_COL - 3] + "..."
        print(f"  {r['scenario_id']:<4} {r['expected']:<10} {r['actual']:<12} [{tag}]  {desc}")

    print("-" * 80)
    print(f"  Result: {passed}/{total} correct")
    print()
    print(f"  Precision : {metrics['precision']:.4f}")
    print(f"  Recall    : {metrics['recall']:.4f}")
    print(f"  F1 Score  : {metrics['f1_score']:.4f}")
    print(f"  Accuracy  : {metrics['accuracy']:.4f}")
    print()
    print(f"  TP={metrics['true_positives']}  FP={metrics['false_positives']}  "
          f"TN={metrics['true_negatives']}  FN={metrics['false_negatives']}")
    print("=" * 80)
    print()


def write_json(results: list[dict], metrics: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "scenarios": results,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"JSON report written to: {output_path}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the lane merge evaluation and produce a metrics report."
    )
    parser.add_argument(
        "--scenarios", nargs="+", metavar="ID",
        default=ALL_SCENARIO_IDS,
        help="Scenario IDs to run (default: all 10).  Example: --scenarios 06 07",
    )
    parser.add_argument(
        "--output", type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Path for the JSON output (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args(argv)

    # Normalise IDs (accept "6" or "06")
    scenario_ids = [sid.zfill(2) for sid in args.scenarios]
    unknown = [sid for sid in scenario_ids if sid not in SCENARIOS]
    if unknown:
        print(f"ERROR: Unknown scenario IDs: {unknown}. "
              f"Valid IDs: {sorted(SCENARIOS)}", file=sys.stderr)
        return 1

    results: list[dict] = []
    for sid in scenario_ids:
        try:
            result = evaluate_scenario(sid)
        except Exception as exc:
            logger.error(f"Scenario {sid} raised an exception: {exc}", exc_info=True)
            spec = SCENARIOS[sid]
            result = {
                "scenario_id":  sid,
                "description":  spec.description,
                "expected":     spec.expected_outcome,
                "actual":       "error",
                "correct":      False,
                "alert_payload": None,
            }
        results.append(result)

    metrics = compute_metrics(results)
    print_report(results, metrics)
    write_json(results, metrics, args.output)

    # Exit code 0 only when all scenarios pass
    return 0 if all(r["correct"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
