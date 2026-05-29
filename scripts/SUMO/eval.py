#!/usr/bin/env python3
"""Generic scenario-pack evaluator.

Runs each scenario of a pack end-to-end (SUMO -> Ditto -> Hono -> detector),
collects the alert published on the pack's MQTT topic, compares it with the
ground-truth label, and reports precision / recall / F1 / accuracy.

A "pack" is any folder under simulations/SUMO/scenarios/ that contains a
pack.py exposing:
  - SUMOCFG, ALERT_TOPIC, ALERT_TIMEOUT_S, END_TIME_S, STEP_LENGTH_S,
    POST_SIM_DRAIN_S, WORKERS, REAL_TIME
  - SCENARIOS: dict[str, ScenarioSpec]   (with .scenario_id, .description,
    .expected_outcome, .route_file)
  - before_scenario(scenario_id) -> None  (optional hook)

Examples
--------
  python scripts/eval.py --pack lanemerge
  python scripts/eval.py --pack lanemerge --scenarios 7 10
  python scripts/eval.py --pack lanemerge --gui --scenarios 1
  python scripts/eval.py --pack lanemerge --output /tmp/eval.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import queue
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import os

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKS_DIR = _REPO_ROOT / "simulations" / "SUMO" / "scenarios"

import bridge  # noqa: E402  (same directory)

load_dotenv(_REPO_ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger("eval")

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))


def _available_packs() -> list[str]:
    if not _PACKS_DIR.exists():
        return []
    return sorted(p.name for p in _PACKS_DIR.iterdir() if (p / "pack.py").exists())


def _load_pack(name: str):
    pack_file = _PACKS_DIR / name / "pack.py"
    if not pack_file.exists():
        sys.exit(
            f"Pack '{name}' not found at {pack_file}.\n"
            f"Available packs: {_available_packs() or '(none)'}"
        )
    mod_name = f"pack_{name}"
    spec = importlib.util.spec_from_file_location(mod_name, pack_file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


@contextmanager
def _alert_collector(topic: str):
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


def _collect_alerts_worst_wins(q: queue.Queue, timeout: float) -> Optional[dict]:
    """Block up to `timeout` for the first alert, drain the queue, return the
    "worst" - any alert with status='unsafe' beats a 'safe', else the last seen.
    """
    alerts: list[dict] = []
    try:
        alerts.append(q.get(timeout=timeout))
    except queue.Empty:
        pass
    while not q.empty():
        alerts.append(q.get())
    if not alerts:
        return None
    for a in alerts:
        if a.get("status") == "unsafe":
            return a
    return alerts[-1]


def evaluate_scenario(pack, spec, gui: bool = False, real_time: bool | None = None) -> dict:
    logger.info(f"[{spec.scenario_id}] {spec.description}")

    before = getattr(pack, "before_scenario", None)
    if callable(before):
        before(spec.scenario_id)

    with _alert_collector(pack.ALERT_TOPIC) as q:
        bridge.run(
            cfg=pack.SUMOCFG,
            route_files=[spec.route_file],
            step_length=pack.STEP_LENGTH_S,
            end_time=pack.END_TIME_S,
            workers=pack.WORKERS,
            gui=gui,
            real_time=pack.REAL_TIME if real_time is None else real_time,
            cleanup=True,
            metrics_interval=60.0,
            post_sim_drain=pack.POST_SIM_DRAIN_S,
        )
        time.sleep(1.0)  # let any trailing MQTT messages arrive
        alert = _collect_alerts_worst_wins(q, pack.ALERT_TIMEOUT_S)

    actual = alert.get("status") if alert else None
    return {
        "scenario_id":   spec.scenario_id,
        "description":   spec.description,
        "expected":      spec.expected_outcome,
        "actual":        actual if actual is not None else "no_alert",
        "correct":       actual == spec.expected_outcome,
        "alert_payload": alert,
    }


def compute_metrics(results: list[dict]) -> dict:
    """Binary classification metrics with 'unsafe' as the positive class."""
    tp = sum(1 for r in results if r["expected"] == "unsafe" and r["actual"] == "unsafe")
    fp = sum(1 for r in results if r["expected"] == "safe"   and r["actual"] == "unsafe")
    tn = sum(1 for r in results if r["expected"] == "safe"   and r["actual"] == "safe")
    fn = sum(1 for r in results if r["expected"] == "unsafe" and r["actual"] != "unsafe")

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1        = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    accuracy  = (tp + tn) / len(results) if results else 0.0

    return {
        "true_positives":  tp,
        "false_positives": fp,
        "true_negatives":  tn,
        "false_negatives": fn,
        "precision":       round(precision, 4),
        "recall":          round(recall, 4),
        "f1_score":        round(f1, 4),
        "accuracy":        round(accuracy, 4),
    }


def print_report(results: list[dict], metrics: dict, pack_name: str) -> None:
    total  = len(results)
    passed = sum(1 for r in results if r["correct"])

    print()
    print(f"  {pack_name} - Evaluation Report")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  {'ID':<4} {'Expected':<10} {'Actual':<12} {'':6} Description")

    for r in results:
        tag  = "PASS" if r["correct"] else "FAIL"
        desc = r["description"]
        if len(desc) > 60:
            desc = desc[:57] + "..."
        print(f"  {r['scenario_id']:<4} {r['expected']:<10} {r['actual']:<12} [{tag}]  {desc}")

    print(f"  Result: {passed}/{total} correct")
    print()
    print(f"  Precision : {metrics['precision']:.4f}")
    print(f"  Recall    : {metrics['recall']:.4f}")
    print(f"  F1 Score  : {metrics['f1_score']:.4f}")
    print(f"  Accuracy  : {metrics['accuracy']:.4f}")
    print()
    print(f"  TP={metrics['true_positives']}  FP={metrics['false_positives']}  "
          f"TN={metrics['true_negatives']}  FN={metrics['false_negatives']}")
    print()


def write_json(results, metrics, output_path: Path, pack_name: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "pack":         pack_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics":      metrics,
        "scenarios":    results,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"JSON report written to: {output_path}")


def _build_parser() -> argparse.ArgumentParser:
    packs = _available_packs()
    p = argparse.ArgumentParser(
        prog="eval.py",
        description="Run a scenario pack end-to-end (SUMO -> Ditto -> detector) "
                    "and report precision / recall / F1 / accuracy.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/SUMO/eval.py --pack lanemerge\n"
            "  python scripts/SUMO/eval.py --pack lanemerge --scenarios 7 10\n"
            "  python scripts/SUMO/eval.py --pack lanemerge --gui --scenarios 1\n"
            "  python scripts/SUMO/eval.py --pack lanemerge --no-real-time --scenarios 1\n"
            "\n"
            f"Available packs: {', '.join(packs) if packs else '(none)'}\n"
            "Each pack lives under simulations/SUMO/scenarios/<name>/ and exposes\n"
            "its scenarios + config in pack.py.\n"
        ),
    )
    p.add_argument("--pack", required=True, metavar="NAME",
                   help="Scenario pack name (folder under simulations/SUMO/scenarios/).")
    p.add_argument("--scenarios", nargs="+", metavar="ID", default=None,
                   help="Scenario IDs to run (default: all in the pack). "
                        "IDs are zero-padded; '7' and '07' both work.")
    p.add_argument("--output", type=Path, default=None, metavar="PATH",
                   help="Where to write the JSON report "
                        "(default: <pack-dir>/results/eval.json).")
    p.add_argument("--gui", action="store_true",
                   help="Open sumo-gui for each scenario (visual inspection; "
                        "best used together with --scenarios on a single ID).")
    p.add_argument("--no-real-time", action="store_true",
                   help="Override the pack's REAL_TIME setting and run SUMO as fast as "
                        "possible. Useful for quick smoke-tests; results may differ since "
                        "detectors rely on wall-clock time for speed calculation.")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        parser.print_help()
        return 0
    args = parser.parse_args(argv)

    pack = _load_pack(args.pack)
    all_ids = sorted(pack.SCENARIOS.keys())

    if args.scenarios:
        scenario_ids = [sid.zfill(2) for sid in args.scenarios]
        unknown = [s for s in scenario_ids if s not in pack.SCENARIOS]
        if unknown:
            print(f"ERROR: Unknown scenario IDs: {unknown}. Valid: {all_ids}",
                  file=sys.stderr)
            return 1
    else:
        scenario_ids = all_ids

    output = args.output or (pack.PACK_DIR / "results" / "eval.json")

    results: list[dict] = []
    for sid in scenario_ids:
        try:
            results.append(evaluate_scenario(
            pack, pack.SCENARIOS[sid],
            gui=args.gui,
            real_time=False if args.no_real_time else None,
        ))
        except Exception as exc:
            logger.error(f"Scenario {sid} raised an exception: {exc}", exc_info=True)
            spec = pack.SCENARIOS[sid]
            results.append({
                "scenario_id":   sid,
                "description":   spec.description,
                "expected":      spec.expected_outcome,
                "actual":        "error",
                "correct":       False,
                "alert_payload": None,
            })

    metrics = compute_metrics(results)
    print_report(results, metrics, args.pack)
    write_json(results, metrics, output, args.pack)
    return 0 if all(r["correct"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
