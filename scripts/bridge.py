#!/usr/bin/env python3
"""SUMO -> Ditto bridge: forwards vehicle GPS from TraCI to Ditto via HTTP PUT."""
from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Set

import requests
from dotenv import load_dotenv

import traci


REPO_ROOT = Path(__file__).resolve().parents[1]
SIM_DIR = REPO_ROOT / "simulations" / "SUMO"
sys.path.insert(0, str(SIM_DIR))
LANEMERGE_CFG = REPO_ROOT / "simulations" / "SUMO" / "lanemerge_eval" / "network" / "lanemerge.sumocfg"
LANEMERGE_SCENARIOS = REPO_ROOT / "simulations" / "SUMO" / "lanemerge_eval" / "scenarios"
_EVAL_STEP = 0.1


def _find_sumo_binary(name: str) -> str:
    """Locate a SUMO binary (sumo / sumo-gui) robustly across install layouts."""
    if sys.platform == "win32":
        name = name if name.endswith(".exe") else name + ".exe"

    # 1. Same dir as the current Python executable (venv Scripts/)
    candidate = Path(sys.executable).parent / name
    if candidate.exists():
        return str(candidate)

    # 2. pip-installed sumo package: search sys.path for sumo/bin/<name>
    for entry in sys.path:
        candidate = Path(entry) / "sumo" / "bin" / name
        if candidate.exists():
            return str(candidate)

    # 3. System PATH
    found = shutil.which(name)
    if found:
        return found

    # 4. Last resort: let the OS try (will raise if not found)
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


def slugify(raw: str) -> str:
    return re.sub(r"[^a-z0-9\-]", "-", raw.lower()).strip("-") or "veh"


class DittoPublisher:
    """Thread-safe Ditto client. One shared policy for all sim vehicles."""

    THING_PREFIX = "org.acme"
    SHARED_POLICY_ID = "org.acme:sumo-sim-policy"

    def __init__(self, api_url: str, auth: tuple, pool_size: int):
        self.api_url = api_url
        self.auth = auth
        self.session = requests.Session()
        self.session.auth = auth
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=pool_size,
            pool_maxsize=pool_size,
            max_retries=0,
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        self.provisioned: Set[str] = set()
        self.provisioned_lock = threading.Lock()

        self.metrics_lock = threading.Lock()
        self.updates_sent = 0
        self.updates_ok = 0
        self.updates_failed = 0
        self.latencies_ms: list[float] = []

    def ensure_shared_policy(self) -> None:
        payload = {
            "policyId": self.SHARED_POLICY_ID,
            "entries": {
                "DEFAULT": {
                    "subjects": {"nginx:ditto": {"type": "generated"}},
                    "resources": {
                        "thing:/": {"grant": ["READ", "WRITE"], "revoke": []},
                        "policy:/": {"grant": ["READ", "WRITE"], "revoke": []},
                        "message:/": {"grant": ["READ", "WRITE"], "revoke": []},
                    },
                    "importable": "implicit",
                },
            },
        }
        r = self.session.put(
            f"{self.api_url}/api/2/policies/{self.SHARED_POLICY_ID}",
            json=payload,
            timeout=10,
        )
        if r.status_code not in (200, 201, 204):
            sys.exit(f"Failed to upsert shared policy: {r.status_code} {r.text}")

    def thing_id(self, vid: str) -> str:
        return f"{self.THING_PREFIX}:sumo-{slugify(vid)}"

    def _provision(self, vid: str, emergency: bool) -> None:
        tid = self.thing_id(vid)
        payload = {
            "policyId": self.SHARED_POLICY_ID,
            "features": {
                "gps": {"properties": {"latitude": 0, "longitude": 0}},
                "info": {"properties": {"emergency": emergency}},
            },
        }
        r = self.session.put(
            f"{self.api_url}/api/2/things/{tid}",
            json=payload,
            timeout=10,
        )
        if r.status_code not in (200, 201, 204):
            raise RuntimeError(f"thing create {tid} failed: {r.status_code} {r.text}")

    def publish(self, vid: str, lat: float, lon: float, emergency: bool) -> None:
        tid = self.thing_id(vid)
        with self.provisioned_lock:
            need_provision = vid not in self.provisioned

        t0 = time.perf_counter()
        try:
            if need_provision:
                self._provision(vid, emergency)
                with self.provisioned_lock:
                    self.provisioned.add(vid)

            body = {
                "gps": {"properties": {"latitude": lat, "longitude": lon}},
                "info": {"properties": {"emergency": emergency}},
            }
            r = self.session.put(
                f"{self.api_url}/api/2/things/{tid}/features",
                json=body,
                timeout=10,
            )
            ok = r.status_code in (200, 204)
            with self.metrics_lock:
                self.updates_sent += 1
                if ok:
                    self.updates_ok += 1
                else:
                    self.updates_failed += 1
                self.latencies_ms.append((time.perf_counter() - t0) * 1000.0)
            if not ok:
                logging.warning("features PUT %s -> %s %s", tid, r.status_code, r.text[:120])
        except Exception as e:
            with self.metrics_lock:
                self.updates_sent += 1
                self.updates_failed += 1
            logging.warning("publish failed for %s: %s", vid, e)

    def delete_all(self) -> None:
        with self.provisioned_lock:
            vids = list(self.provisioned)
        logging.info("Deleting %d simulated things from Ditto...", len(vids))
        for vid in vids:
            # Send (0,0) to trigger cleanup in position_processor
            self.publish(vid, 0.0, 0.0, False)
            tid = self.thing_id(vid)
            try:
                self.session.delete(f"{self.api_url}/api/2/things/{tid}", timeout=10)
            except Exception as e:
                logging.warning("delete %s failed: %s", tid, e)

    def metrics_snapshot(self) -> dict:
        with self.metrics_lock:
            lat = sorted(self.latencies_ms)
            n = len(lat)
            snapshot = {
                "sent": self.updates_sent,
                "ok": self.updates_ok,
                "failed": self.updates_failed,
                "avg_ms": (sum(lat) / n) if n else 0.0,
                "p50_ms": lat[n // 2] if n else 0.0,
                "p95_ms": lat[int(n * 0.95)] if n else 0.0,
            }
            self.latencies_ms.clear()
            return snapshot


def run(args: argparse.Namespace, extra_sumo_flags: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    env = load_env()
    pub = DittoPublisher(env["DITTO_API_URL"], env["DITTO_AUTH"], args.workers)
    pub.ensure_shared_policy()

    sumo_binary = _find_sumo_binary("sumo-gui" if args.gui else "sumo")
    cfg_path = getattr(args, "cfg", None) or str(SIM_DIR / "osm.sumocfg")
    sumo_cmd = [
        sumo_binary,
        "-c", cfg_path,
        "--step-length", str(args.step_length),
        "--no-warnings",
        "--tripinfo-output", os.devnull,
        "--statistic-output", os.devnull,
    ]
    if args.end_time is not None:
        sumo_cmd += ["--end", str(args.end_time)]
    if extra_sumo_flags:
        sumo_cmd += extra_sumo_flags

    logging.info("Starting SUMO: %s", " ".join(sumo_cmd))
    traci.start(sumo_cmd)

    executor = ThreadPoolExecutor(max_workers=args.workers)

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

            # cap to first N for determinism
            if args.max_vehicles and len(vehicle_ids) > args.max_vehicles:
                vehicle_ids_pub = vehicle_ids[: args.max_vehicles]
                skipped_due_to_cap += len(vehicle_ids) - args.max_vehicles
            else:
                vehicle_ids_pub = vehicle_ids

            max_concurrent = max(max_concurrent, len(vehicle_ids_pub))

            for vid in vehicle_ids_pub:
                x, y = traci.vehicle.getPosition(vid)
                lon, lat = traci.simulation.convertGeo(x, y)
                vtype = traci.vehicle.getTypeID(vid)
                emergency = "emergency" in vtype.lower()
                seen.add(vid)
                executor.submit(pub.publish, vid, lat, lon, emergency)

            if args.real_time:
                elapsed = time.time() - loop_t0
                remaining = args.step_length - elapsed
                if remaining > 0:
                    time.sleep(remaining)

            step_idx += 1
            now = time.time()
            if now - last_metrics_t >= args.metrics_interval:
                m = pub.metrics_snapshot()
                logging.info(
                    "[step=%d sim=%.0fs active=%d seen=%d cap_skip=%d | sent=%d ok=%d fail=%d avg=%.1fms p50=%.1fms p95=%.1fms]",
                    step_idx, sim_time, len(vehicle_ids_pub), len(seen), skipped_due_to_cap,
                    m["sent"], m["ok"], m["failed"], m["avg_ms"], m["p50_ms"], m["p95_ms"],
                )
                last_metrics_t = now

            if args.max_steps and step_idx >= args.max_steps:
                logging.info("Reached --max-steps=%d, stopping.", args.max_steps)
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
        drain = getattr(args, "post_sim_drain", 0.0)
        if drain > 0:
            logging.info("Draining pipeline for %.1f s...", drain)
            time.sleep(drain)
        if args.cleanup:
            pub.delete_all()


def run_scenario(scenario_id: str, spec, gui: bool = False) -> None:
    rou_file = LANEMERGE_SCENARIOS / f"scenario_{scenario_id}.rou.xml"
    if not rou_file.exists():
        raise FileNotFoundError(f"Route file not found: {rou_file}")

    extra = ["--route-files", str(rou_file)]
    if not gui:
        extra.append("--no-step-log")

    args = argparse.Namespace(
        cfg=str(LANEMERGE_CFG),
        workers=4,
        step_length=_EVAL_STEP,
        end_time=120,
        max_vehicles=None,
        max_steps=None,
        real_time=gui,
        metrics_interval=60.0,
        gui=gui,
        cleanup=True,
        post_sim_drain=5.0,
    )
    run(args, extra_sumo_flags=extra)


def main() -> None:
    p = argparse.ArgumentParser(description="SUMO -> Ditto bridge")
    p.add_argument("--workers", type=int, default=16,
                   help="HTTP thread pool size (default 16)")
    p.add_argument("--step-length", type=float, default=1.0,
                   help="SUMO step length in seconds (default 1.0)")
    p.add_argument("--end-time", type=float, default=None,
                   help="SUMO --end value (cuts sim early). Default: cfg value")
    p.add_argument("--max-steps", type=int, default=None,
                   help="Stop after this many sim steps (wall-clock safety)")
    p.add_argument("--max-vehicles", type=int, default=None,
                   help="Publish at most N vehicles per step (drops the rest)")
    p.add_argument("--metrics-interval", type=float, default=2.0,
                   help="Seconds between metrics lines")
    p.add_argument("--real-time", action="store_true",
                   help="Sleep to pace sim to real time (avoids flooding Ditto)")
    p.add_argument("--gui", action="store_true", help="Run sumo-gui")
    p.add_argument("--cleanup", action="store_true",
                   help="DELETE all things we created on exit")
    run(p.parse_args())


if __name__ == "__main__":
    main()
