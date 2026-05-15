#!/usr/bin/env python3
"""
Compares end-to-end response times for two GPS injection paths:

  Hono path:   MQTT TLS -> Hono adapter -> Ditto twin update
               -> Ditto WebSocket event -> position_processor -> cars/updates

  Ditto direct: HTTP PUT to Ditto REST API -> Ditto WebSocket event
               -> position_processor -> cars/updates

The script subscribes to cars/updates on the internal Mosquitto broker and
correlates each incoming CarUpdate with the send that triggered it using the
injected (latitude, longitude) pair.

Usage:
    python tests/performance/hono_vs_ditto.py --car perf-car
    python tests/performance/hono_vs_ditto.py --car perf-car --iterations 50 --warmup 3

Requires:
    - Docker Compose stack running (Hono, Ditto, Mosquitto, position_processor)
    - A device provisioned with simulations/create_car.py (produces devices/<car>.json)
    - Environment variables from .env (DITTO_API_URL, DITTO_USER, DITTO_PASS,
      MQTT_ADAPTER_IP, MQTT_ADAPTER_PORT_MQTTS)
"""

import argparse
import json
import os
import ssl
import statistics
import sys
import threading
import time
from pathlib import Path

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

# Base coordinates for Aveiro; each update steps by this delta so lat/lon is unique.
_BASE_LAT = 40.6405
_BASE_LON = -8.6538
_DELTA    = 0.00001


def _load_meta(car: str) -> dict:
    path = REGISTRY_DIR / f"{car}.json"
    if not path.exists():
        sys.exit(f"Device metadata not found: {path}\nRun: python simulations/create_car.py {car}")
    return json.loads(path.read_text())


# Subscriber

class UpdateListener:
    def __init__(self, host: str, port: int):
        self._lock      = threading.Lock()
        self._pending: dict[tuple, float] = {}
        self._latencies: list[float] = []

        client = mqtt.Client(client_id="perf-hono-ditto-sub", protocol=mqtt.MQTTv311)
        client.on_connect = lambda c, _u, _f, rc: c.subscribe("cars/updates", qos=0) if rc == 0 else None
        client.on_message = self._on_message
        client.connect(host, port, keepalive=10)
        client.loop_start()
        self._client = client
        time.sleep(0.5)

    def _on_message(self, _c, _u, msg):
        t_recv = time.time()
        try:
            payload = json.loads(msg.payload.decode())
            key = (round(payload.get("latitude", 0), 8), round(payload.get("longitude", 0), 8))
            with self._lock:
                t_send = self._pending.pop(key, None)
            if t_send is not None:
                self._latencies.append((t_recv - t_send) * 1000.0)
        except Exception:
            pass

    def expect(self, lat: float, lon: float, t_send: float) -> None:
        with self._lock:
            self._pending[(round(lat, 8), round(lon, 8))] = t_send

    def wait_for_count(self, n: int, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if len(self._latencies) >= n:
                    return True
            time.sleep(0.05)
        return False

    def latencies(self) -> list[float]:
        with self._lock:
            return list(self._latencies)

    def clear(self) -> None:
        with self._lock:
            self._latencies.clear()
            self._pending.clear()

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()


# Ditto direct injection

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


def run_ditto_direct(meta: dict, listener: UpdateListener, n: int, interval: float) -> list[float]:
    session = requests.Session()
    session.auth = DITTO_AUTH

    results: list[float] = []
    for i in range(n):
        lat = _BASE_LAT + i * _DELTA
        lon = _BASE_LON + i * _DELTA
        t0 = time.time()
        listener.expect(lat, lon, t0)
        _ditto_put(meta["thing_id"], lat, lon, session)
        time.sleep(interval)

    # wait up to 10 s for all replies
    if listener.wait_for_count(n, timeout=15.0):
        results = listener.latencies()
    else:
        results = listener.latencies()
        print(f"  warning: only {len(results)}/{n} updates arrived within timeout")

    listener.clear()
    return results


# Hono MQTT injection

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


def run_hono_mqtt(meta: dict, listener: UpdateListener, n: int, interval: float) -> list[float]:
    if not HONO_HOST:
        print("  warning: MQTT_ADAPTER_IP not set, skipping Hono path")
        return []

    client = _make_hono_client(meta)
    connected = threading.Event()
    client.on_connect = lambda c, _u, _f, rc: connected.set() if rc == 0 else None
    client.connect(HONO_HOST, HONO_PORT, keepalive=30)
    client.loop_start()
    if not connected.wait(timeout=10):
        client.loop_stop()
        print("  warning: could not connect to Hono MQTT adapter")
        return []

    results: list[float] = []
    for i in range(n):
        lat = _BASE_LAT + i * _DELTA
        lon = _BASE_LON + i * _DELTA
        feature_value = {
            "gps":  {"properties": {"latitude": lat, "longitude": lon}},
            "info": {"properties": {"emergency": False}},
        }
        payload = json.dumps({
            "topic":   f"{meta['thing_id'].replace(':', '/')}/things/twin/commands/modify",
            "headers": {},
            "path":    "/features/",
            "value":   feature_value,
        })
        t0 = time.time()
        listener.expect(lat, lon, t0)
        client.publish("telemetry", payload, qos=1)
        time.sleep(interval)

    if listener.wait_for_count(n, timeout=15.0):
        results = listener.latencies()
    else:
        results = listener.latencies()
        print(f"  warning: only {len(results)}/{n} updates arrived within timeout")

    listener.clear()
    client.loop_stop()
    client.disconnect()
    return results


# Reporting

def _stats(lats: list[float]) -> dict:
    if not lats:
        return {}
    s = sorted(lats)
    n = len(s)
    return {
        "n":      n,
        "min":    round(min(s), 1),
        "avg":    round(statistics.mean(s), 1),
        "median": round(statistics.median(s), 1),
        "p95":    round(s[int(n * 0.95)], 1) if n >= 10 else None,
        "p99":    round(s[int(n * 0.99)], 1) if n >= 50 else None,
        "max":    round(max(s), 1),
    }


def _print_side_by_side(ditto: dict, hono: dict) -> None:
    print()
    print("-" * 52)
    print(f"  {'Metric':<14} {'Ditto direct':>16} {'Hono MQTT':>16}")
    print("-" * 52)

    for key in ("n", "min", "avg", "median", "p95", "p99", "max"):
        d_val = ditto.get(key)
        h_val = hono.get(key)
        d_str = str(d_val) if d_val is not None else "n/a"
        h_str = str(h_val) if h_val is not None else "n/a"
        unit  = " ms" if key not in ("n",) else ""
        print(f"  {key:<14} {d_str + unit:>16} {h_str + unit:>16}")

    print("-" * 52)

    if ditto and hono and ditto.get("median") and hono.get("median"):
        overhead = round(hono["median"] - ditto["median"], 1)
        sign = "+" if overhead >= 0 else ""
        print(f"  Hono overhead (median):  {sign}{overhead} ms")

    print()


def _diagnose(ditto: dict, hono: dict) -> None:
    issues = []
    if ditto.get("p95") and ditto["p95"] > 500:
        issues.append("Ditto direct p95 > 500 ms - position_processor or Overpass API is slow")
    if hono.get("median") and ditto.get("median") and hono["median"] > ditto["median"] * 3:
        issues.append("Hono overhead is > 3x Ditto direct - check Hono AMQP link or device registry")
    if not issues:
        print("  Both paths within expected range.")
    else:
        print("  Observations:")
        for issue in issues:
            print(f"    - {issue}")
    print()


# CLI

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compare Hono vs Ditto direct injection latency")
    p.add_argument("--car",        required=True, help="Car name used with create_car.py")
    p.add_argument("--iterations", type=int,   default=30,   help="Updates per path (default: 30)")
    p.add_argument("--warmup",     type=int,   default=3,    help="Warmup updates before measurement (default: 3)")
    p.add_argument("--interval",   type=float, default=0.8,  help="Seconds between updates (default: 0.8)")
    p.add_argument("--mqtt-host",  default="localhost")
    p.add_argument("--mqtt-port",  type=int,   default=1884)
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    meta = _load_meta(args.car)

    print()
    print("  pei-automotive / Hono vs Ditto injection comparison")
    print(f"  car:        {meta['thing_id']}")
    print(f"  iterations: {args.iterations}  warmup: {args.warmup}  interval: {args.interval} s")
    print(f"  broker:     {args.mqtt_host}:{args.mqtt_port}")
    print()

    listener = UpdateListener(args.mqtt_host, args.mqtt_port)

    try:
        # Warmup: position_processor needs at least 2 positions before it can
        # compute speed/heading and publish a CarUpdate. Use Ditto direct for warmup.
        print(f"  [1/4] warming up ({args.warmup} updates via Ditto direct) ...")
        session = requests.Session()
        session.auth = DITTO_AUTH
        for i in range(args.warmup):
            lat = _BASE_LAT - (i + 1) * _DELTA
            lon = _BASE_LON - (i + 1) * _DELTA
            _ditto_put(meta["thing_id"], lat, lon, session)
            time.sleep(args.interval)
        listener.clear()
        time.sleep(1.0)

        # Ditto direct measurement
        print(f"  [2/4] measuring Ditto direct ({args.iterations} updates) ...")
        ditto_lats = run_ditto_direct(meta, listener, args.iterations, args.interval)
        print(f"        received {len(ditto_lats)}/{args.iterations}")
        time.sleep(1.0)

        # Hono MQTT measurement
        print(f"  [3/4] measuring Hono MQTT ({args.iterations} updates) ...")
        hono_lats = run_hono_mqtt(meta, listener, args.iterations, args.interval)
        print(f"        received {len(hono_lats)}/{args.iterations}")

    finally:
        listener.stop()

    # Results
    print()
    print("  [4/4] results")
    ditto_stats = _stats(ditto_lats)
    hono_stats  = _stats(hono_lats)
    _print_side_by_side(ditto_stats, hono_stats)
    _diagnose(ditto_stats, hono_stats)


if __name__ == "__main__":
    main()
