#!/usr/bin/env python3
"""
Compares two GPS injection paths using the pipeline monitor as the measurement source.

  Ditto direct: HTTP PUT to Ditto REST API
                -> Ditto WebSocket event -> position_processor -> cars/updates

  Hono path:    MQTT TLS -> Hono adapter -> Ditto twin update
                -> Ditto WebSocket event -> position_processor -> cars/updates

Latency is measured by the pipeline monitor (d2p: Ditto WebSocket → position_processor).
The pipeline must be running with Ditto configured before this script is started.
"""

import argparse
import json
import os
import ssl
import sys
import threading
import time
import warnings
from pathlib import Path
from typing import Optional

warnings.filterwarnings("ignore", category=DeprecationWarning)

import paho.mqtt.client as mqtt
import requests
import certifi
from dotenv import load_dotenv

load_dotenv()

DITTO_API  = os.getenv("DITTO_API_URL", "").rstrip("/")
DITTO_AUTH = (os.getenv("DITTO_USER", "ditto"), os.getenv("DITTO_PASS", "ditto"))
HONO_HOST  = os.getenv("MQTT_ADAPTER_IP", "")
HONO_PORT  = int(os.getenv("MQTT_ADAPTER_PORT_MQTTS", "8883"))

REGISTRY_DIR = Path(__file__).resolve().parents[2] / "simulations" / "devices"

_BASE_LAT = 40.6405
_BASE_LON = -8.6538
_DELTA    = 0.00001


def _load_meta(car: str) -> dict:
    path = REGISTRY_DIR / f"{car}.json"
    if not path.exists():
        sys.exit(f"Device metadata not found: {path}\nRun: python simulations/create_car.py {car}")
    return json.loads(path.read_text())


# Injection helpers
def _ditto_put(thing_id: str, lat: float, lon: float, session: requests.Session) -> None:
    body = {
        "gps":  {"properties": {"latitude": lat, "longitude": lon}},
        "info": {"properties": {"emergency": False}},
    }
    r = session.put(
        f"{DITTO_API}/api/2/things/{thing_id}/features",
        json=body,
        timeout=10,
    )
    if r.status_code not in (200, 201, 204):
        raise RuntimeError(f"Ditto PUT failed: {r.status_code} {r.text[:120]}")


def run_ditto_direct(meta: dict, n: int, interval: float) -> int:
    session = requests.Session()
    session.auth = DITTO_AUTH
    sent = 0
    for i in range(n):
        lat = _BASE_LAT + i * _DELTA
        lon = _BASE_LON + i * _DELTA
        try:
            _ditto_put(meta["thing_id"], lat, lon, session)
            sent += 1
        except Exception as e:
            print(f"  warning: PUT failed at iteration {i}: {e}")
        time.sleep(interval)
    return sent


def _make_hono_client(meta: dict) -> mqtt.Client:
    cert_hint = meta.get("ca_cert")
    ca_certs = cert_hint if cert_hint and Path(cert_hint).exists() else certifi.where()
    client = mqtt.Client(protocol=mqtt.MQTTv311)
    client.username_pw_set(f"{meta['auth_id']}@{meta['hono_tenant']}", meta["password"])
    client.tls_set(
        ca_certs=ca_certs,
        cert_reqs=ssl.CERT_NONE,
        tls_version=ssl.PROTOCOL_TLSv1_2,
    )
    client.tls_insecure_set(True)
    return client


def run_hono_mqtt(meta: dict, n: int, interval: float) -> int:
    if not HONO_HOST:
        print("  warning: MQTT_ADAPTER_IP not set, skipping Hono path")
        return 0

    client = _make_hono_client(meta)
    connected = threading.Event()
    client.on_connect = lambda c, _u, _f, rc: connected.set() if rc == 0 else None
    client.connect(HONO_HOST, HONO_PORT, keepalive=30)
    client.loop_start()
    if not connected.wait(timeout=10):
        client.loop_stop()
        print("  warning: could not connect to Hono MQTT adapter")
        return 0

    sent = 0
    for i in range(n):
        lat = _BASE_LAT + i * _DELTA
        lon = _BASE_LON + i * _DELTA
        payload = json.dumps({
            "topic":   f"{meta['thing_id'].replace(':', '/')}/things/twin/commands/modify",
            "headers": {},
            "path":    "/features/",
            "value": {
                "gps":  {"properties": {"latitude": lat, "longitude": lon}},
                "info": {"properties": {"emergency": False}},
            },
        })
        result = client.publish("telemetry", payload, qos=1)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            sent += 1
        time.sleep(interval)

    client.loop_stop()
    client.disconnect()
    return sent


# Pipeline integration
def _pipeline_check(url: str) -> bool:
    for attempt in range(6):
        try:
            r = requests.get(f"{url}/api/snapshot", timeout=5)
            if r.status_code != 200:
                sys.exit(f"Pipeline not reachable at {url}")
            if r.json().get("ditto_ok"):
                return True
        except SystemExit:
            raise
        except Exception as e:
            sys.exit(f"Cannot reach pipeline at {url}: {e}")
        if attempt < 5:
            print(f"  waiting for Ditto connection ... ({attempt + 1}/5)")
            time.sleep(3)
    sys.exit(
        "Pipeline is running but Ditto is not connected after 15 s.\n"
        "Start the pipeline with: python3 server.py --ditto-ws <WS_URL>"
    )


def _pipeline_reset(url: str) -> None:
    requests.post(f"{url}/api/reset", timeout=5)


def _pipeline_fetch(url: str, want_n: int, timeout: float = 30.0) -> Optional[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{url}/api/metrics/perf", timeout=5)
            if r.status_code == 200:
                data = r.json()
                d2p = (data.get("pipeline_ms") or {}).get("ditto_to_processor") or {}
                if (d2p.get("n") or 0) >= want_n:
                    return data
        except Exception:
            pass
        time.sleep(0.5)
    try:
        r = requests.get(f"{url}/api/metrics/perf", timeout=5)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


# Reporting
def _print_results(ditto_perf: Optional[dict], hono_perf: Optional[dict], n: int) -> None:
    def _d2p(perf: Optional[dict]) -> dict:
        if not perf:
            return {}
        return (perf.get("pipeline_ms") or {}).get("ditto_to_processor") or {}

    dd2p = _d2p(ditto_perf)
    hd2p = _d2p(hono_perf)

    W = 62
    print()
    print("-" * W)
    print(f"  {'Metric':<20} {'Ditto direct':>18} {'Hono MQTT':>18}")
    print("-" * W)
    print(f"  Pipeline d2p   (Ditto WebSocket → position_processor publish)")

    for key in ("n", "avg", "min", "p95", "max"):
        d_val = dd2p.get(key)
        h_val = hd2p.get(key)
        unit  = " ms" if key != "n" else f"  (injected: {n})"
        d_str = (str(d_val) + unit) if d_val is not None else "n/a"
        h_str = (str(h_val) + unit) if h_val is not None else "n/a"
        print(f"    {key:<18} {d_str:>18} {h_str:>18}")

    print("-" * W)

    if dd2p.get("avg") and hd2p.get("avg"):
        overhead = round(hd2p["avg"] - dd2p["avg"], 1)
        sign = "+" if overhead >= 0 else ""
        print(f"  Hono overhead (avg d2p):  {sign}{overhead} ms")
    print()

    issues = []
    if dd2p.get("p95") and dd2p["p95"] > 100:
        issues.append(
            f"d2p p95 {dd2p['p95']} ms - Overpass cache likely cold; "
            "re-run on the same area to warm it up"
        )
    if dd2p.get("avg") and hd2p.get("avg") and hd2p["avg"] > dd2p["avg"] * 3:
        issues.append("Hono d2p > 3x Ditto direct - check Hono AMQP link or device registry")

    if not issues:
        print("  Both paths within expected range.")
    else:
        print("  Observations:")
        for issue in issues:
            print(f"    - {issue}")
    print()


# CLI
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare Hono vs Ditto injection latency via pipeline monitor")
    p.add_argument("--car",          required=True)
    p.add_argument("--iterations",   type=int,   default=30,  help="Updates per path (default: 30)")
    p.add_argument("--warmup",       type=int,   default=3,   help="Warmup updates before measurement (default: 3)")
    p.add_argument("--interval",     type=float, default=0.8, help="Seconds between updates (default: 0.8)")
    p.add_argument("--pipeline-url", required=True,           help="Pipeline monitor URL, e.g. http://localhost:8765")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    meta = _load_meta(args.car)

    print()
    print("  pei-automotive / Hono vs Ditto injection comparison")
    print(f"  car:        {meta['thing_id']}")
    print(f"  iterations: {args.iterations}  warmup: {args.warmup}  interval: {args.interval} s")
    print(f"  pipeline:   {args.pipeline_url}")
    print()

    _pipeline_check(args.pipeline_url)

    # Warmup
    # Step 1: let position_processor compute speed/heading (needs >=2 positions)
    # Step 2: warm the Overpass tile cache for the measurement coordinate area so
    #         both phases see cache hits and d2p is comparable.
    #         All 50 measurement coords span ~55 m - one tile covers them all.
    print(f"  [1/4] warming up (position_processor + Overpass tile cache) ...")
    session = requests.Session()
    session.auth = DITTO_AUTH
    for i in range(args.warmup):
        _ditto_put(meta["thing_id"], _BASE_LAT - (i + 1) * _DELTA, _BASE_LON - (i + 1) * _DELTA, session)
        time.sleep(args.interval)
    print(f"        warming Overpass cache for measurement area ...")
    _ditto_put(meta["thing_id"], _BASE_LAT, _BASE_LON, session)
    time.sleep(args.interval)
    _ditto_put(meta["thing_id"], _BASE_LAT + _DELTA, _BASE_LON + _DELTA, session)
    time.sleep(2.0)  # allow the Overpass HTTP fetch to complete
    time.sleep(1.0)

    # Ditto direct
    print(f"  [2/4] measuring Ditto direct ({args.iterations} updates) ...")
    _pipeline_reset(args.pipeline_url)
    ditto_sent = run_ditto_direct(meta, args.iterations, args.interval)
    print(f"        injected {ditto_sent}/{args.iterations}  -  waiting for pipeline ...")
    ditto_perf = _pipeline_fetch(args.pipeline_url, ditto_sent, timeout=30.0)
    time.sleep(1.0)

    # Hono MQTT
    print(f"  [3/4] measuring Hono MQTT ({args.iterations} updates) ...")
    _pipeline_reset(args.pipeline_url)
    hono_sent = run_hono_mqtt(meta, args.iterations, args.interval)
    print(f"        injected {hono_sent}/{args.iterations}  -  waiting for pipeline ...")
    hono_perf = _pipeline_fetch(args.pipeline_url, hono_sent, timeout=30.0)

    print()
    print("  [4/4] results")
    _print_results(ditto_perf, hono_perf, args.iterations)


if __name__ == "__main__":
    main()
