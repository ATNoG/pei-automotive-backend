"""Ditto client for SUMO simulations.

Manages the full lifecycle of ephemeral Ditto Things created during a SUMO run:
shared policy provisioning, lazy per-vehicle Thing creation, GPS publishing,
throughput metrics, and bulk cleanup on exit.

Vehicles are unknown in advance — Things are provisioned on first sight and
identified as org.acme:sumo-<slug>. This is distinct from test vehicles, which
are pre-registered via create_car.py and tracked in devices/<name>.json.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Set

import requests

from send_position_ditto import put_features


def slugify(raw: str) -> str:
    return re.sub(r"[^a-z0-9\-]", "-", raw.lower()).strip("-") or "veh"


class DittoPublisher:
    """Thread-safe Ditto client. One shared policy for all sim vehicles."""

    THING_PREFIX = "org.acme"
    SHARED_POLICY_ID = "org.acme:sumo-sim-policy"

    def __init__(self, api_url: str, auth: tuple, pool_size: int):
        self.api_url = api_url
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
        self._provisioned_lock = threading.Lock()

        self._metrics_lock = threading.Lock()
        self.updates_sent = 0
        self.updates_ok = 0
        self.updates_failed = 0
        self._latencies_ms: list[float] = []

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
            raise RuntimeError(f"failed to upsert shared policy: {r.status_code} {r.text}")

    def thing_id(self, vid: str) -> str:
        return f"{self.THING_PREFIX}:sumo-{slugify(vid)}"

    def _provision(self, vid: str, emergency: bool) -> None:
        tid = self.thing_id(vid)
        r = self.session.put(
            f"{self.api_url}/api/2/things/{tid}",
            json={
                "policyId": self.SHARED_POLICY_ID,
                "features": {
                    "gps": {"properties": {"latitude": 0, "longitude": 0}},
                    "info": {"properties": {"emergency": emergency}},
                },
            },
            timeout=10,
        )
        if r.status_code not in (200, 201, 204):
            raise RuntimeError(f"thing create {tid} failed: {r.status_code} {r.text}")

    def publish(self, vid: str, lat: float, lon: float, emergency: bool) -> None:
        tid = self.thing_id(vid)
        with self._provisioned_lock:
            need_provision = vid not in self.provisioned

        t0 = time.perf_counter()
        try:
            if need_provision:
                self._provision(vid, emergency)
                with self._provisioned_lock:
                    self.provisioned.add(vid)

            put_features(self.session, self.api_url, tid, lat, lon, emergency)

            with self._metrics_lock:
                self.updates_sent += 1
                self.updates_ok += 1
                self._latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        except Exception as e:
            with self._metrics_lock:
                self.updates_sent += 1
                self.updates_failed += 1
            logging.warning("publish failed for %s: %s", vid, e)

    def delete_all(self) -> None:
        with self._provisioned_lock:
            vids = list(self.provisioned)
        logging.info("deleting %d simulated things from Ditto...", len(vids))
        for vid in vids:
            self.publish(vid, 0.0, 0.0, False)
            try:
                self.session.delete(f"{self.api_url}/api/2/things/{self.thing_id(vid)}", timeout=10)
            except Exception as e:
                logging.warning("delete %s failed: %s", vid, e)

    def metrics_snapshot(self) -> dict:
        with self._metrics_lock:
            lat = sorted(self._latencies_ms)
            n = len(lat)
            snapshot = {
                "sent": self.updates_sent,
                "ok": self.updates_ok,
                "failed": self.updates_failed,
                "avg_ms": (sum(lat) / n) if n else 0.0,
                "p50_ms": lat[n // 2] if n else 0.0,
                "p95_ms": lat[int(n * 0.95)] if n else 0.0,
            }
            self._latencies_ms.clear()
            return snapshot
