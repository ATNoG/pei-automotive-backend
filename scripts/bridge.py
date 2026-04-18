#!/usr/bin/env python3
"""
SUMO -> Ditto bridge.

Runs the SUMO simulation via TraCI and forwards every vehicle's GPS position
straight to Eclipse Ditto via HTTP PUT /api/2/things/.../features. The
position_processor service picks up the resulting WS event and the normal
pipeline (speed/heading/speed_limit enrichment -> cars/updates -> detectors)
runs unchanged.

We bypass Hono entirely for the simulation because the per-call MQTT+TLS
handshake + Python subprocess startup in simulations/send_position.py makes
it impossible to drive more than a handful of cars in real time.

Fast path per vehicle update:
    1 HTTP PUT to Ditto (kept-alive session, pooled) ~50-250 ms over the VPN.
    Run in parallel across a ThreadPoolExecutor so N cars/step fit inside the
    sim step length.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Optional, Set

import requests
from dotenv import load_dotenv

import traci


REPO_ROOT = Path(__file__).resolve().parents[3]
SIM_DIR = Path(__file__).resolve().parent


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


def run(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    env = load_env()
    pub = DittoPublisher(env["DITTO_API_URL"], env["DITTO_AUTH"], args.workers)
    pub.ensure_shared_policy()

    binary_name = "sumo-gui" if args.gui else "sumo"
    # Prefer the binary that ships with the active venv's eclipse-sumo wheel;
    # fall back to PATH.
    venv_bin = Path(sys.executable).parent / binary_name
    sumo_binary = str(venv_bin) if venv_bin.exists() else binary_name
    sumo_cmd = [
        sumo_binary,
        "-c", str(SIM_DIR / "osm.sumocfg"),
        "--step-length", str(args.step_length),
        "--no-warnings",
    ]
    if args.end_time is not None:
        sumo_cmd += ["--end", str(args.end_time)]

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

            # Cap the set of vehicles we publish (determinism: pick the first N).
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

            # Real-time pacing (optional).
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
        logging.info("Shutting down executor (flush in-flight)...")
        executor.shutdown(wait=True, cancel_futures=False)
        m = pub.metrics_snapshot()
        logging.info("Final: seen=%d max_concurrent=%d sent=%d ok=%d fail=%d",
                     len(seen), max_concurrent, m["sent"], m["ok"], m["failed"])
        if args.cleanup:
            pub.delete_all()


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
