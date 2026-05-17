#!/usr/bin/env python3
"""
Pipeline latency test.

Measures three timestamps per GPS injection:
  t0  PUT sent to Ditto
  t1  cars/raw_updates/<car_id> received  (Ditto WS → ProximityFilter)
  t2  cars/updates/<car_id>    received  (PositionProcessor)

Usage:
  python3 measure_latency.py --cars 5 --duration 60 --rate 1.0
"""

import argparse
import json
import os
import queue
import sys
import threading
import time

import paho.mqtt.client as mqtt
import requests
from dotenv import load_dotenv

load_dotenv()

DITTO_API  = os.getenv("DITTO_API_URL", "").rstrip("/")
DITTO_AUTH = (os.getenv("DITTO_USER", "ditto"), os.getenv("DITTO_PASS", "ditto"))

_NS     = "org.acme"
_PREFIX = "load-car"


def _car_id(n: int) -> str:
    return f"{_PREFIX}-{n:03d}"


def _thing_id(n: int) -> str:
    return f"{_NS}:{_car_id(n)}"


class Subscriber:
    """MQTT client subscribed to exact per-car raw_updates and updates topics."""

    def __init__(self, host: str, port: int, car_ids: set[str]) -> None:
        self._raw  = {cid: queue.Queue() for cid in car_ids}
        self._proc = {cid: queue.Queue() for cid in car_ids}
        self._car_ids = car_ids

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.connect(host, port, keepalive=10)
        client.loop_start()
        self._client = client

    def _on_connect(self, client, _userdata, _flags, _reason, _props) -> None:
        for cid in self._car_ids:
            client.subscribe(f"cars/raw_updates/{cid}", qos=0)
            client.subscribe(f"cars/updates/{cid}", qos=0)

    def _on_message(self, _client, _userdata, msg) -> None:
        t = time.time()
        try:
            cid = json.loads(msg.payload).get("car_id")
        except Exception:
            return
        if not cid:
            return
        if msg.topic.startswith("cars/raw_updates/") and cid in self._raw:
            self._raw[cid].put(t)
        elif msg.topic.startswith("cars/updates/") and cid in self._proc:
            self._proc[cid].put(t)

    def wait(self, q: queue.Queue, after: float, timeout: float = 15.0) -> float | None:
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            try:
                t = q.get(timeout=min(remaining, 0.2))
                if t > after:
                    return t
            except queue.Empty:
                pass

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()


def _worker(
    n: int, rate: float, stop_ev: threading.Event,
    sub: Subscriber, records: list, lock: threading.Lock,
    session: requests.Session,
) -> None:
    cid, tid = _car_id(n), _thing_id(n)
    period = 1.0 / max(rate, 0.001)
    seq = 0

    while not stop_ev.is_set():
        lat = 40.6405 + (seq % 500) * 0.00001
        lon = -8.6538 + (seq % 500) * 0.00001

        t0 = time.time()
        try:
            r = session.put(
                f"{DITTO_API}/api/2/things/{tid}/features",
                json={"gps": {"properties": {"latitude": lat, "longitude": lon}},
                      "info": {"properties": {"emergency": False}}},
                timeout=15,
            )
            if r.status_code not in (200, 201, 204):
                stop_ev.wait(timeout=period)
                continue
        except Exception:
            stop_ev.wait(timeout=period)
            continue

        t1 = sub.wait(sub._raw[cid],  t0)
        t2 = sub.wait(sub._proc[cid], t1 or t0)

        with lock:
            records.append({
                "ditto_raw_ms": round((t1 - t0) * 1000, 1) if t1 else None,
                "raw_proc_ms":  round((t2 - t1) * 1000, 1) if (t1 and t2) else None,
                "e2e_ms":       round((t2 - t0) * 1000, 1) if t2 else None,
            })

        stop_ev.wait(timeout=max(0.0, period - (time.time() - t0)))
        seq += 1


def _stats(vals: list[float]) -> dict:
    if not vals:
        return {}
    s = sorted(vals)
    n = len(s)
    return {
        "n":   n,
        "avg": round(sum(s) / n, 1),
        "p50": round(s[n // 2], 1),
        "p95": round(s[int(n * 0.95)], 1),
        "max": round(s[-1], 1),
    }


def _print_results(records: list[dict]) -> None:
    segments = {
        "ditto_raw_ms": "Ditto → raw_updates  (ProximityFilter)",
        "raw_proc_ms":  "raw_updates → updates  (PositionProcessor)",
        "e2e_ms":       "E2E  (total)",
    }
    dropped = sum(1 for r in records if r["e2e_ms"] is None)
    print(f"\n  {len(records)} measurements, {dropped} dropped\n")
    print(f"  {'segment':<42}  {'n':>5}  {'avg':>8}  {'p50':>8}  {'p95':>8}  {'max':>8}")
    print(f"  {'-'*42}  {'-'*5}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}")
    for key, label in segments.items():
        st = _stats([r[key] for r in records if r.get(key) is not None])
        if not st:
            print(f"  {label:<42}  {'—':>5}  {'—':>8}  {'—':>8}  {'—':>8}  {'—':>8}")
        else:
            print(f"  {label:<42}  {st['n']:>5}  {st['avg']:>7.0f}ms  {st['p50']:>7.0f}ms  {st['p95']:>7.0f}ms  {st['max']:>7.0f}ms")
    print()


def main() -> None:
    p = argparse.ArgumentParser(description="Pipeline latency test")
    p.add_argument("--cars",        type=int,   default=5,         help="simulated cars (default: 5)")
    p.add_argument("--duration",    type=int,   default=60,        help="test duration in seconds (default: 60)")
    p.add_argument("--rate",        type=float, default=1.0,       help="injections/sec per car (default: 1.0)")
    p.add_argument("--broker-host", default="localhost",           help="MQTT broker host (default: localhost)")
    p.add_argument("--broker-port", type=int,   default=1884,      help="MQTT broker port (default: 1884)")
    args = p.parse_args()

    if not DITTO_API:
        sys.exit("DITTO_API_URL not set")

    print(f"\n  pipeline latency  |  cars={args.cars}  duration={args.duration}s  rate={args.rate}/s")
    print(f"  ditto={DITTO_API}  broker={args.broker_host}:{args.broker_port}\n")

    session = requests.Session()
    session.auth = DITTO_AUTH

    print(f"  [1/3] creating {args.cars} Ditto things ...", end="", flush=True)
    errors, lock0 = [], threading.Lock()

    def _create(i: int) -> None:
        r = session.put(f"{DITTO_API}/api/2/things/{_thing_id(i)}",
                        json={"attributes": {"created_by": "measure_latency"}}, timeout=15)
        if r.status_code not in (200, 201, 204):
            with lock0:
                errors.append(f"{_thing_id(i)}: {r.status_code}")

    threads = [threading.Thread(target=_create, args=(i,), daemon=True) for i in range(args.cars)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=20)
    if errors:
        sys.exit("\n  failed:\n" + "\n".join(errors))
    print(" done")

    car_ids = {_car_id(i) for i in range(args.cars)}
    sub = Subscriber(args.broker_host, args.broker_port, car_ids)
    time.sleep(0.5)  # wait for subscriptions to register

    print(f"  [2/3] running {args.duration}s ...")
    records, lock, stop_ev = [], threading.Lock(), threading.Event()
    sessions = [requests.Session() for _ in range(args.cars)]
    for s in sessions:
        s.auth = DITTO_AUTH

    workers = [
        threading.Thread(target=_worker,
                         args=(i, args.rate, stop_ev, sub, records, lock, sessions[i]),
                         daemon=True)
        for i in range(args.cars)
    ]
    for w in workers: w.start()

    t_start = time.time()
    while time.time() - t_start < args.duration:
        with lock: n_rec = len(records)
        print(f"    {time.time()-t_start:>4.0f}s  {n_rec} measurements", end="\r", flush=True)
        time.sleep(2)

    stop_ev.set()
    for w in workers: w.join(timeout=20)
    sub.stop()

    print(f"\n  [3/3] results")
    _print_results(records)


if __name__ == "__main__":
    main()
