#!/usr/bin/env python3
"""SUMO -> Ditto bridge.

Drives a SUMO simulation via TraCI and publishes vehicle positions to Eclipse
Ditto over HTTP. Knows nothing about evaluation or scenario packs - those
live in scripts/SUMO/eval.py and simulations/SUMO/scenarios/<pack>/.

Examples
--------
  # Drive the Barra random traffic into Ditto
  python scripts/SUMO/bridge.py --cfg simulations/SUMO/osm.sumocfg

  # Drive a single lane-merge scenario route file (manual one-off run)
  python scripts/SUMO/bridge.py \\
      --cfg simulations/SUMO/scenarios/lanemerge/network/lanemerge.sumocfg \\
      --route-files simulations/SUMO/scenarios/lanemerge/scenarios/scenario_07.rou.xml \\
      --step-length 0.5 --end-time 120 --real-time --cleanup
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Set

from dotenv import load_dotenv
import traci

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "simulations"))
from ditto_sumo import DittoPublisher  # noqa: E402


def _find_sumo_binary(name: str) -> str:
    """Locate a SUMO binary (sumo / sumo-gui) robustly across install layouts."""
    if sys.platform == "win32":
        name = name if name.endswith(".exe") else name + ".exe"

    candidate = Path(sys.executable).parent / name
    if candidate.exists():
        return str(candidate)

    for entry in sys.path:
        candidate = Path(entry) / "sumo" / "bin" / name
        if candidate.exists():
            return str(candidate)

    found = shutil.which(name)
    if found:
        return found

    return name


def load_env() -> dict:
    load_dotenv(REPO_ROOT / ".env")
    required = ["DITTO_API_URL", "DITTO_USER", "DITTO_PASS"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        sys.exit(f"Missing env vars: {missing}. Source {REPO_ROOT / '.env'} or export them.")
    return {
        "DITTO_API_URL": os.environ["DITTO_API_URL"].rstrip("/"),
        "DITTO_AUTH": (os.environ["DITTO_USER"], os.environ["DITTO_PASS"]),
    }


def run(
    cfg: Path,
    *,
    route_files: list[Path] | None = None,
    step_length: float = 1.0,
    end_time: float | None = None,
    workers: int = 16,
    gui: bool = False,
    real_time: bool = False,
    cleanup: bool = False,
    max_vehicles: int | None = None,
    max_steps: int | None = None,
    metrics_interval: float = 2.0,
    post_sim_drain: float = 0.0,
) -> DittoPublisher:
    """Run a SUMO cfg through TraCI and publish vehicle GPS to Ditto.

    Returns the DittoPublisher so callers can inspect final metrics.
    Knows nothing about scenarios - callers compose this however they need.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    env = load_env()
    pub = DittoPublisher(env["DITTO_API_URL"], env["DITTO_AUTH"], workers)
    pub.ensure_shared_policy()

    sumo_binary = _find_sumo_binary("sumo-gui" if gui else "sumo")
    sumo_cmd = [
        sumo_binary, "-c", str(cfg),
        "--step-length", str(step_length),
        "--no-warnings",
        "--tripinfo-output", os.devnull,
        "--statistic-output", os.devnull,
    ]
    if end_time is not None:
        sumo_cmd += ["--end", str(end_time)]
    if route_files:
        sumo_cmd += ["--route-files", ",".join(str(p) for p in route_files)]
    if gui:
        sumo_cmd += ["--quit-on-end", "--start"]
    else:
        sumo_cmd += ["--no-step-log"]

    logging.info("Starting SUMO: %s", " ".join(sumo_cmd))
    traci.start(sumo_cmd)

    executor = ThreadPoolExecutor(max_workers=workers)
    last_metrics_t = time.time()
    step_idx = 0
    seen: Set[str] = set()
    max_concurrent = 0
    skipped_due_to_cap = 0

    try:
        while traci.simulation.getMinExpectedNumber() > 0:
            loop_t0 = time.time()
            traci.simulationStep()
            sim_time = traci.simulation.getTime()
            vehicle_ids = traci.vehicle.getIDList()

            if max_vehicles and len(vehicle_ids) > max_vehicles:
                vehicle_ids_pub = vehicle_ids[:max_vehicles]
                skipped_due_to_cap += len(vehicle_ids) - max_vehicles
            else:
                vehicle_ids_pub = vehicle_ids

            max_concurrent = max(max_concurrent, len(vehicle_ids_pub))

            for vid in vehicle_ids_pub:
                x, y = traci.vehicle.getPosition(vid)
                lon, lat = traci.simulation.convertGeo(x, y)
                emergency = "emergency" in traci.vehicle.getTypeID(vid).lower()
                seen.add(vid)
                executor.submit(pub.publish, vid, lat, lon, emergency)

            if real_time:
                elapsed = time.time() - loop_t0
                if step_length - elapsed > 0:
                    time.sleep(step_length - elapsed)

            step_idx += 1
            now = time.time()
            if now - last_metrics_t >= metrics_interval:
                m = pub.metrics_snapshot()
                logging.info(
                    "[step=%d sim=%.0fs active=%d seen=%d cap_skip=%d | sent=%d ok=%d fail=%d avg=%.1fms p50=%.1fms p95=%.1fms]",
                    step_idx, sim_time, len(vehicle_ids_pub), len(seen), skipped_due_to_cap,
                    m["sent"], m["ok"], m["failed"], m["avg_ms"], m["p50_ms"], m["p95_ms"],
                )
                last_metrics_t = now

            if max_steps and step_idx >= max_steps:
                logging.info("Reached --max-steps=%d, stopping.", max_steps)
                break
    except KeyboardInterrupt:
        logging.info("Interrupted.")
    finally:
        logging.info("Closing TraCI...")
        try:
            traci.close()
        except Exception:
            pass
        logging.info("Shutting down executor...")
        executor.shutdown(wait=True, cancel_futures=False)
        m = pub.metrics_snapshot()
        logging.info("Final: seen=%d max_concurrent=%d sent=%d ok=%d fail=%d",
                     len(seen), max_concurrent, m["sent"], m["ok"], m["failed"])
        if post_sim_drain > 0:
            logging.info("Draining pipeline for %.1f s...", post_sim_drain)
            time.sleep(post_sim_drain)
        if cleanup:
            pub.delete_all()

    return pub


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bridge.py",
        description="SUMO -> Ditto bridge. Runs a SUMO sim via TraCI and publishes "
                    "vehicle GPS to Eclipse Ditto over HTTP.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Barra random traffic\n"
            "  python scripts/SUMO/bridge.py --cfg simulations/SUMO/osm.sumocfg\n"
            "\n"
            "  # Single lane-merge scenario route file\n"
            "  python scripts/SUMO/bridge.py \\\n"
            "      --cfg simulations/SUMO/scenarios/lanemerge/network/lanemerge.sumocfg \\\n"
            "      --route-files simulations/SUMO/scenarios/lanemerge/scenarios/scenario_07.rou.xml \\\n"
            "      --step-length 0.5 --end-time 120 --real-time --cleanup\n"
            "\n"
            "To run a full evaluation across all scenarios of a pack, use scripts/SUMO/eval.py.\n"
        ),
    )
    p.add_argument("--cfg", required=True, type=Path, metavar="PATH")
    p.add_argument("--route-files", type=Path, nargs="+", metavar="PATH", default=None)
    p.add_argument("--step-length", type=float, default=1.0, metavar="SECONDS")
    p.add_argument("--end-time", type=float, default=None, metavar="SECONDS")
    p.add_argument("--workers", type=int, default=16, metavar="N")
    p.add_argument("--max-steps", type=int, default=None, metavar="N")
    p.add_argument("--max-vehicles", type=int, default=None, metavar="N")
    p.add_argument("--metrics-interval", type=float, default=2.0, metavar="SECONDS")
    p.add_argument("--post-sim-drain", type=float, default=0.0, metavar="SECONDS")
    p.add_argument("--real-time", action="store_true")
    p.add_argument("--gui", action="store_true")
    p.add_argument("--cleanup", action="store_true")
    return p


def main() -> int:
    parser = _build_parser()
    if len(sys.argv) == 1:
        parser.print_help()
        return 0
    args = parser.parse_args()
    run(
        cfg=args.cfg,
        route_files=args.route_files,
        step_length=args.step_length,
        end_time=args.end_time,
        workers=args.workers,
        gui=args.gui,
        real_time=args.real_time,
        cleanup=args.cleanup,
        max_vehicles=args.max_vehicles,
        max_steps=args.max_steps,
        metrics_interval=args.metrics_interval,
        post_sim_drain=args.post_sim_drain,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
